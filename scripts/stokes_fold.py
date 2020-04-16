#!/usr/bin/env python3

import logging
import argparse
import sys
import os
import numpy as np
import glob
import config
import psrqpy

from job_submit import submit_slurm
import data_processing_pipeline as dpp
import plotting_toolkit
import binfinder
import rm_synthesis

logger = logging.getLogger(__name__)

#get ATNF db location
try:
    ATNF_LOC = os.environ['PSRCAT_FILE']
except:
    logger.warn("ATNF database could not be loaded on disk. This may lead to a connection failure")
    ATNF_LOC = None

comp_config = config.load_config_file()

#---------------------------------------------------------------
class NotFoundError(Exception):
    """Raise when a value is not found in a file"""
    pass

def plot_everything(pulsar, obsid, run_dir, freq, ascii_archive=None, rvm_dict=None, chi_map=None, rm=None, rm_e=None):
    """
    Plots polarimetry, RVM fits, chi map and stacked profiles

    Parameters:
    -----------
    pulsar: string
        The J name of the pulsar
    obsid: int
        The observation ID
    run_dir: string
        The path of the directory containing the required files for plotting
    freq: float
        The frequency of the observation in MHz
    ascii_archive: string
        OPTIONAL - The name of the archive. If none, will try to find it in run_dir
    rvm_dict: dictionary
        OPTIONAL - The dictionary from read_rvm_fit_file. Used to plot RVM fits and chi_map. Default: None
    """
    if not ascii_archive:
        try:
            ascii_archive = glob.glob(run_dir + "/*archive.txt")[0]
        except IndexError("No ascii archive found. Cannot plot"):
            sys.exit(1)

    logger.info("Plotting dspsr archive {0} in {1}".format(ascii_archive, run_dir))
    ascii_path = os.path.join(run_params.pointing_dir, ascii_archive)

    logger.info("Plotting polarimetry profile without RVM fit")
    fig_name = plotting_toolkit.plot_archive_stokes(ascii_path, obsid=obsid, pulsar=pulsar,\
                        freq=freq, out_dir=run_dir, rm=rm, rm_e=rm_e)
    if rvm_dict:
        if rvm_dict["nbins"]>=5:
            logger.info("Plotting polarimetry profile with RVM fit")
            plotting_toolkit.plot_archive_stokes(ascii_path, obsid=obsid, pulsar=pulsar,\
                            freq=freq, out_dir=run_dir, rvm_fit=rvm_dict, rm=rm, rm_e=rm_e)
        else:
            logger.info("Not enough PA points to plot RVM fit")

    if chi_map and rvm_dict:
        logger.info("Plotting RVM fit chi squared map")
        dof = rvm_dict["dof"]
        chis = np.copy(chi_map["chis"])
        chis = chis/dof
        chi_map_name = "{0}/{1}_{2}_RVM_reduced_chi_map.png".format(run_dir, obsid, pulsar)
        plotting_toolkit.plot_rvm_chi_map(chi_map["chis"][:], chi_map["alphas"][:], chi_map["zetas"][:],\
                        name=chi_map_name, dof=dof, my_chi=rvm_dict["redchisq"], my_zeta=rvm_dict["zeta"]*np.pi/180,\
                        my_alpha=rvm_dict["alpha"]*np.pi/180)

    logger.info("Plotting stacked archival profiles")
    #retrieve epn data
    try:
        pulsar_dict = plotting_toolkit.get_data_from_epndb(pulsar)
        #read my data
        pulsar_dict, my_lin_pol = plotting_toolkit.add_ascii_to_dict(pulsar_dict, ascii_archive, freq)
        #ignore any frequencies > 15 000 MHz
        ignore_freqs = []
        for f in pulsar_dict["freq"]:
            if f>15000:
                ignore_freqs.append(f)
        #plot intensity stack
        plotting_toolkit.plot_stack(pulsar_dict["freq"][:], pulsar_dict["Iy"][:], pulsar,\
                out_dir=run_dir, special_freqs=[freq], ignore_freqs=ignore_freqs)
        #clip anything without stokes
        pulsar_dict = plotting_toolkit.clip_nopol_epn_data(pulsar_dict)
        #get lin pol - but don't change ours because it could have been generated from psrchive
        lin = plotting_toolkit.lin_pol_from_dict(pulsar_dict)
        for i, f in enumerate(pulsar_dict["freq"]):
            if f==freq:
                lin[i]=my_lin_pol
        #plot the polarimetry stack
        plotting_toolkit.plot_stack_pol(pulsar_dict["freq"], pulsar_dict["Iy"], lin, pulsar_dict["Vy"], pulsar,\
                out_dir=run_dir, ignore_freqs=ignore_freqs)
    except plotting_toolkit.NoEPNDBError:
        logger.info("Pulsar not on the EPN database")


def find_RM_from_cat(pulsar):
    """
    Gets rotation measure from prscat query. Returns None if not on catalogue

    Parameters:
    -----------
    pulsar: str
        The J-name of the pulsar

    Returns:
    --------
    rm: float
        The rotation measure
    rm_err: float
        The uncertainty in the rotation measure
    """

    query = psrqpy.QueryATNF(params=["RM"], psrs=[pulsar], loadfromdb=ATNF_LOC).pandas
    rm = query["RM"][0]
    rm_err = query["RM_ERR"][0]

    if np.isnan(rm):
        return None, None
    elif np.isnan(rm_err):
        rm_err = 0.15*rm
    return rm, rm_err

def find_RM_from_file(fname):
    """
    Finds the rotation measure from an input filename as generated by rmfit.
    Returns Nones if rm cold not be generates.

    Parameters:
    -----------
    fname: str
        The path to the file

    Returns:
    --------
    rm: float
        The rotation measure from the file
    rm_err: float
        The uncertainty in the rotation measure
    """
    f = open(fname)
    lines=f.readlines()
    f.close()
    rm=None
    rm_err=None
    for line in lines:
        line = line.split()
        if line[0] == "Best" and line[1] == "RM":
            rm=float(line[3])
            if len(line) >= 5:
                rm_err=float(line[5])
            else:
                logger.warn("Uncertainty for RM not available")
                rm_err=None
            break

    if not rm:
        logger.warn("RM could not be generated from archive file")

    return rm, rm_err

def read_rvm_fit_file(filename):
    """
    Reads a file with the output from psrmodel and returns a dictionary of the results.
    Raises NotFoundError if an expected value is not present in the file

    Parameters:
    -----------
    filename: str
        The pathname of the file with the rvm fit

    Returns:
    --------
    rvm_dict: dictionary
        contains keys:
            nbins: int
                The number of bins used in the rvm fit
            psi_0: float
                The derived psi_0 parameter
            psi_0_e: float
                The uncertainty in psi_0
            zeta: float
                The derived zeta parameter
            zeta_e: float
                The uncertainty in zeta
            alpha: float
                The derived alpha parameter
            alpha_e:
                The uncertainty in alpha
            phi_0: float
                The derived phi_0 parameter
            phi_0_e: float
                The uncertainty in phi_0
            redchisq: float
                The reduced chi square of the best fit
            dof: int
                The degrees of freedom of the fit
    """
    keylist = ("nbins", "redchisq", "dof", "psi_0", "psi_0_e", "zeta", "zeta_e",\
                "alpha", "alpha_e", "phi_0",  "phi_0_e")
    rvm_dict={}
    for key in keylist:
        rvm_dict[key]=None
    f = open(filename)
    lines = f.readlines()
    f.close()
    n_elements = 0
    for i, line in enumerate(lines):
        if line.endswith("bins\n"):
            rvm_dict["nbins"] = int(line.split()[-2])
        elif line[0:6] == "chisq=":
            rvm_dict["redchisq"] = float(line.split()[-1])
            rvm_dict["dof"] = int(line.split()[0].split("=")[-1])
        elif line[0:7] == "psi_0=(":
            psi_0_str = line.split()[0].split("=")[-1].split("(")[-1].split(")")[0].split("+")
            rvm_dict["psi_0"] = float(psi_0_str[0])
            rvm_dict["psi_0_e"] = abs(float(psi_0_str[-1]))
        elif line[0:7] == "zeta =(":
            zeta_str = line.split()[1].split("(")[-1].split(")")[0].split("+")
            rvm_dict["zeta"]  = float(zeta_str[0])
        elif line[0:7] == "alpha=(":
            alpha_str = line.split()[0].split("(")[-1].split(")")[0].split("+")
            rvm_dict["alpha"]  = float(alpha_str[0])
        elif line[0:7] == "phi_0=(":
            phi_0_str = line.split()[0].split("(")[-1].split(")")[0].split("+")
            rvm_dict["phi_0"]  = float(phi_0_str[0])
            rvm_dict["phi_0_e"]  = abs(float(phi_0_str[-1]))
        elif line[0:6] == "alpha=":
            n_elements += 1

    rvm_dict["alpha_e"]  = 180/np.sqrt(n_elements)/2
    rvm_dict["zeta_e"]  = 180/np.sqrt(n_elements)/2

    for key in keylist:
        if rvm_dict[key] is None:
            raise NotFoundError("{0} not found in file: {1}".format(key, filename))

    return rvm_dict

def read_chi_map(map):
    """
    Reads a chi map of an RVM fit output by psrmodel

    Parameters:
    -----------
    map: str
        The pathname of the map to read

    Returns:
    --------
    chi_map: dictionary
        contains keys:
            alphas: list
                The alpha values in radians
            zetas: list
                The zeta values in radians
            chis: list
                The chi values corresponding to the alpha/zeta pairs
    """
    f = open(map)
    lines = f.readlines()
    f.close()
    alphas = []
    zetas = []
    chis = []
    for line in lines:
        if not line == "\n":
            alphas.append(float(line.split()[0]))
            zetas.append(float(line.split()[1]))
            chis.append(float(line.split()[2]))

    #convert to radians
    for i, a, z in zip(range(len(alphas)), alphas, zetas):
        alphas[i] = a*np.pi/180
        zetas[i] = z*np.pi/180
    chi_map = {"alphas":alphas, "zetas":zetas, "chis":chis}
    return chi_map

def analytic_pa(phi, alpha, zeta, psi_0, phi_0):
    #Inputs should be in radians
    numerator = np.sin(alpha) * np.sin(phi - phi_0)
    denominator = np.sin(zeta) * np.cos(alpha) - np.cos(zeta) * np.sin(alpha) * np.cos(phi - phi_0)
    return np.arctan2(numerator,denominator) + psi_0

def add_rvm_to_commands(run_dir, archive_name, out_name="RVM_fit.txt", commands=None, res=90):
    """
    Adds the RVM fitting commands to a list

    run_dir: str
        The direcrory to run the commands in
    archive_name: str
        The name of the archive file to fit
    out_name: str
        OPTIONAL - The name of the output text file. Default: RVM_fit.txt
    commands: list
        OPTIONAL - A list to append the commands to. Default: None
    res: int
        OPTIONAL - The number of solutions to trial for both alpha and beta. Default: 90

    Returns:
    --------
    commands: list
        A list of commands with the RVM fitting commands appended
    """
    if not commands:
        commands = []
    if not archive_name[-4:] == ".ar2" and not archive_name[-3:] == ".ar":
        archive_name += ".ar2"

    commands.append("cd {}".format(run_dir))
    commands.append("echo 'Fitting RVM'")
    commands.append("psrmodel {0} -resid -psi-resid -x -s {1}X{1} &> {2} > chi_map.txt".format(archive_name, res, out_name))

    return commands

def add_rm_cor_to_commands(run_dir, archive_name, RM, ascii_name="rm_fit_ascii_archive.txt", commands=None):
    """
    Adds the commands to correct an archive for the rotation measure of the pulsar

    Parameters:
    -----------
    run_dir: str
        The directory to run the comamnds in
    archive_name: str
        The name of the archive file to correct
    RM: float
        The rotation measure to correct for
    ascii_name: str
        OPTIONAL - The name of the ascii text file to write the corrected archive to. Default: rm_fit_ascii_archive.txt
    commands: list
        OPTIONAL - A list to append the commands to. Default: None

    Returns:
    --------
    commands: list
        A list of commands with the rm correction commands appended
    """
    if not commands:
        commands = []
    if not archive_name[-3:] == ".ar":
        archive_name += ".ar"
    if not RM:
        raise ValueError("RM is not a valid value: {}".format(RM))

    #correct for RM
    commands.append("cd {}".format(run_dir))
    commands.append("echo 'Correcting for input rotation measure: {}'".format(RM))
    commands.append("pam -e ar2 -R {0} {1}".format(RM, archive_name))

    #Turn the archive into a readable ascii file
    commands.append("echo 'Wiritng result to text file'")
    commands.append("pdv -FTtlZ {0} > {1}".format(archive_name+"2", ascii_name))

    return commands

def add_rm_fit_to_commands(pulsar, run_dir, archive_name, out_name=None, commands=None):
    """
    Adds the commands to find the rotation measure of an archive

    Parameters:
    -----------
    puslar: str
        The J name of the pulsar
    run_dir: str
        The directory to run the commands in
    archvie_name: str
        The name of the archive to fit the rotation measure to
    out_name: str
        The name of the output text file. Default: *pulsar*_rmfit.txt
    commands: list
        OPTIONAL - A list to append the commands to. Default: None

    Returns:
    --------
    commands: list
        A list of commands with the rm fitting commands appended
    """
    if not commands:
        commands = []
    if not out_name:
        out_name = os.path.join(run_dir, "{}_rmfit.txt".format(pulsar))
    if not archive_name[-3:] == ".ar":
        archive_name += ".ar"

    commands.append("cd {}".format(run_dir))
    commands.append("echo 'Attempting to find rotation measure.\nOutputting result to {}'".format(out_name))
    commands.append("rmfit {0} -t > {1}".format(archive_name, out_name))

    return commands

def add_rmsynth_to_commands(run_dir, archive_name, label="", write=True, plot=True, keep_QUV=False, commands=None):
    """
    Adds the commands to perform RM synthesis

    Parameters:
    -----------
    run_dir: string
        The location to run the commands
    archive_name: string
        The name of the archive (.ar) to run on
    lebel: string
        A label to apply to the output files. Default: ""
    write: boolean
        OPTIONAL - If True, will write the results of rm_synthesis to file. Default: True
    plot: boolean
        OPTIONAL - If True, will plot the RM synthesis. Default: True
    keep_QUV: boolean
        OPTIONAL - If True, will keep the QUVflux.out file from rmfit. Default: False
    commands: list
        A list of commands to append the rmsynth commands to. Default: None
    """
    if commands is None:
        commands = []

    rms_coms = "rm_synthesis.py"
    rms_coms += " -f {}".format(archive_name)
    if label:
        rms_coms += " --label {}".format(label)
    if write:
        rms_coms += " --write"
    if plot:
        rms_coms += " --plot"
    if keep_QUV:
        rms_coms += " --keep_QUV"
    rms_coms += " --force_single"

    commands.append("cd {}".format(run_dir))
    commands.append("echo 'perfoming RM synthesis'")
    commands.append(rms_coms)

    return commands

def add_pfb_inversion_to_commands(run_dir, pulsar, obsid, \
                                nbins=1024, seek=None, total=None, commands=None, tscrunch=100, dm=None, period=None):
    """
    Adds a small dspsr folding pipeline to a list of commands.
    This will fold on each channel using .hdr files, combine all channels and then output the profile as an ascii text file.

    run_dir: string
        The directory to work in. Typically the pointing directory.
    puslar: string
        The J name of the pulsar
    nbins: int
        OPTIONAL - The number of bins to fold with. Default: 1024
    seek: int
        OPTIONAL - The number of seconds into the obs to start folding. Default: None
    total : int
        OPTIONAL - The total number of seconds of data to fold. Default: None
    commands: list
        OPTIONAL - A list of commands to add the dspsr commands to. Default: None
    tscrunch: int
        OPTIONAL - The number of seconds to timescrunch. Default: 100
    dm: float
        OPTIONAL - The dm to fold around. Default=None
    period: float
        OPTIONAL - The period to fold around. Default=None

    Returns:
    --------
    commands: list
        A list of commands with the dspsr inverse pfb bash commands included
    """

    if commands is None:
        commands = []

    dspsr_coms = "dspsr -U 8000 -A -cont -no_dyn"
    dspsr_coms += " -L {}".format(tscrunch)
    dspsr_coms += " -E {}.eph".format(pulsar)
    dspsr_coms += " -b {}".format(nbins)
    if dm:
        dspsr_coms += " -D {}".format(dm)
    if period:
        dspsr_coms += " -c {}".format(period)
    if seek and total:
        dspsr_coms += " -S {0} -T {1}".format(seek, total)

    psradd_coms = "psradd -R -m time {0}_{1}_inverse_pfb*ar -o {0}_{1}_ipfb_archive.ar".format(obsid, pulsar)

    commands.append("cd {0}".format(run_dir))
    commands.append("psrcat -e {0} > {0}.eph".format(pulsar))
    commands.append("echo 'Folding on vdif files'")
    commands.append("j=0")
    commands.append("for i in *.hdr;")
    commands.append("   do {0} -O {1}_{2}_inverse_pfb_$j $i ;".format(dspsr_coms, obsid, pulsar))
    commands.append("   j=$((j+1))")
    commands.append("done;")
    commands.append("echo 'Combining archives'")
    commands.append(psradd_coms)
    commands.append("echo 'Converting to ascii text'")
    commands.append("pdv -FTtlZ {0}_{1}_ipfb_archive.ar > {0}_{1}_ipfb_archive.txt".format(obsid, pulsar))

    return commands

def add_dspsr_fold_to_commands(pulsar, run_dir, nbins,\
                                out_name=None, commands=None, seek=None, total=None, subint=None, dspsr_ops="", no_ephem=False,\
                                dm=None, period=None):
    """
    Adds a dspsr folding command to a list of commands

    Parameters:
    -----------
    pulsar: str
        The J name of the pulsar
    run_dir: str
        The directory to run the folding operation in
    nbins: int
        The number of bins to fold with
    out_name: str
        OPTIONAL - The name of the output archive file. Default: *pulsar*_archive.ar
    commands: list
        OPTIONAL - A list to add the folding commands to
    seek: int
        OPTIONAL - In seconds, where to begin folding. If none, will not use. Default: None
    total: int
        OPTIONAL - In seconds, the duration to integrate over. If none, will not use. Default: None
    subint: float
        OPTIONAL - In seconds, the length of the sub integrations. Default: 10.
    dspsr_ops: str
        OPTIONAL - A string containing any custom options for dspsr to use. Default: ""
    no_ephem: boolean
        OPTIONAL - Whether to override the ephemeris with custom folding instructions. Default: False
    dm: float
        OPTIONAL - The dm to fold around. Default=None
    period: float
        OPTIONAL - The period to fold around. Default=None

    Returns:
    --------
    Commmands: list
        A list with the dspsr folding commands appended

    """
    if not out_name:
        out_name = "{}_archive".format(pulsar)
    if dspsr_ops!='':
        logger.info("dspsr custom options: {}".format(dspsr_ops))
    if not commands:
        commands = []

    dspsr_ops += " {}/*.fits".format(run_dir)
    dspsr_ops += " -O {}".format(os.path.join(run_dir, out_name))
    dspsr_ops += " -b {}".format(nbins)
    if dm:
        dspsr_ops += " -D {}".format(dm)
    if period:
        dspsr_ops += " -c {}".format(period)
    if not no_ephem:
        dspsr_ops += " -E {}.eph".format(os.path.join(run_dir, pulsar))
    if subint:
        dspsr_ops += " -L {}".format(subint)
    if seek:
        dspsr_ops += " -S {}".format(seek)
    if total:
        dspsr_ops += " -T {}".format(total)

    commands.append("cd {}".format(run_dir))
    if not run_params.no_ephem:
        commands.append("psrcat -e {1} > {0}/{1}.eph".format(run_dir, pulsar))
    commands.append("echo 'Running DSPSR folding...'")
    commands.append("dspsr -cont -U 8000 -A -K {}".format(dspsr_ops))

    return commands

def submit_inverse_pfb_fold(run_params, stop=False):
    """
    Submits the inverse pfb folding script and fits RM

    Parameters:
    -----------
    run_params: object
        The run_params object defined in data_processing_pipeline

    Returns:
    --------
    job_id: int
        The id of the submitted job
    """
    #Find beam coverage for known pulsars
    if not run_params.cand:
        enter, leave, _ = binfinder.find_fold_times\
                        (run_params.pulsar, run_params.obsid, run_params.beg, run_params.end, min_z_power=[0.3, 0.1])
        obs_int = run_params.end - run_params.beg
        if enter is None or leave is None:
            logger.warn("{} not in beam for given times. Will use entire integration time to fold.".format(run_params.pulsar))
            logger.warn("Used the following parameters:")
            logger.warn("pulsar: {}".format(run_params.pulsar))
            logger.warn("obsid: {}".format(run_params.obsid))
            logger.warn("beg: {}".format(run_params.beg))
            logger.warn("end: {}".format(run_params.end))
            enter_sec = 0
            duration = obs_int
        else:
            duration = (leave - enter) * obs_int
            enter_sec = enter * duration
            logger.info("{0} enters beam at {1} and leaves at {2}".format(run_params.pulsar, enter, leave))
            logger.info("Integration time: {}".format(duration))
    else:
        enter_sec = None
        duration = None
    #pfb inversion
    duration = run_params.end - run_params.beg
    commands = add_pfb_inversion_to_commands(run_params.pointing_dir, run_params.pulsar, run_params.obsid, seek=enter_sec, total=duration,\
                                            tscrunch=duration)
    #launch RM fitting
    archive_name = "{0}_{1}_ipfb_archive.ar".format(run_params.obsid, run_params.pulsar)
    rmfit_name = "{0}_{1}_ipfb_rmfit.txt".format(run_params.obsid, run_params.pulsar)
    commands = add_rm_fit_to_commands(run_params.pulsar, run_params.pointing_dir, archive_name, out_name=rmfit_name, commands=commands)

    #launch RM synthesis
    mylabel = "{0}_{1}".format(run_params.puslar, run_params.obsid)
    commands = add_rmsynth_to_commands(run_params.pointing_dir, archive_name, write=True, plot=True, keep_QUV=False, label=mylabel, commands=commands)

    if not stop:
        #Relaunch stokes_fold.py
        launch_line = dpp.stokes_launch_line(run_params)
        commands.append(launch_line)
    elif not run_params.stop:
        launch_line = dpp.stokes_launch_line(run_params)
        commands.append(launch_line)

    batch_dir = os.path.join(comp_config['base_product_dir'], run_params.obsid, "batch")
    name = "inverse_pfb_{0}_{1}_{2}".format(run_params.obsid, run_params.pulsar, run_params.stokes_bins)

    logger.info("Submitting inverse pfb job:")
    logger.info("Pointing directory: {}".format(run_params.pointing_dir))
    logger.info("Pulsar name: {}".format(run_params.pulsar))
    logger.info("Job name: {}".format(name))

    job_id = submit_slurm(name, commands,\
                        batch_dir=batch_dir,\
                        slurm_kwargs={"time": "10:00:00"},\
                        module_list=['mwa_search/{0}'.format(run_params.mwa_search),\
                                    "dspsr", "psrchive"],\
                        submit=True, vcstools_version="{0}".format(run_params.vcs_tools))

    return job_id

def submit_dspsr_rmfit(run_params):
    """
    Runs dspsr on fits files and relaunches the stokes fold script

    Parameters:
    -----------
    run_params: object
        The run_params object from data_processing_pipeline.py
    """
    if not run_params.cand:
        enter, leave, _ = binfinder.find_fold_times\
                        (run_params.pulsar, run_params.obsid, run_params.beg, run_params.end, min_z_power=[0.3, 0.1])
        obs_int = run_params.end - run_params.beg
        if enter is None or leave is None:
            logger.warn("{} not in beam for given times. Will use entire integration time to fold.".format(run_params.pulsar))
            logger.warn("Used the following parameters:")
            logger.warn("pulsar: {}".format(run_params.pulsar))
            logger.warn("obsid: {}".format(run_params.obsid))
            logger.warn("beg: {}".format(run_params.beg))
            logger.warn("end: {}".format(run_params.end))
            enter_sec = 0
            duration = obs_int
        else:
            duration = (leave - enter) * obs_int
            enter_sec = enter * duration
            logger.info("{0} enters beam at {1} and leaves at {2}".format(run_params.pulsar, enter, leave))
            logger.info("Integration time: {}".format(duration))
    else:
        enter_sec = None
        duration = None

    #dspsr command
    file_name = "{0}_{1}_archive".format(run_params.obsid, run_params.pulsar)
    commands = add_dspsr_fold_to_commands(run_params.pulsar, run_params.pointing_dir, run_params.stokes_bins, out_name=file_name,\
                                        seek=enter_sec, total=duration, subint=run_params.subint, dspsr_ops=run_params.dspsr_ops,\
                                        no_ephem=run_params.no_ephem, dm=run_params.dm, period=run_params.period)
    #rmfit command
    out_name = "{0}_{1}_rmfit.txt".format(run_params.obsid, run_params.pulsar)
    commands = add_rm_fit_to_commands(run_params.pulsar, run_params.pointing_dir, file_name, out_name=out_name, commands=commands)

    #rmsynth command
    mylabel = "{0}_{1}".format(run_params.pulsar, run_params.obsid)
    commands = add_rmsynth_to_commands(run_params.pointing_dir, file_name+".ar", write=True, plot=True, keep_QUV=False, commands=commands)

    #rerun the script
    if not run_params.stop:
        launch_line = dpp.stokes_launch_line(run_params)
        commands.append(launch_line)

    name = "DSPSR_RMfit_{0}_{1}_{2}".format(run_params.pulsar, run_params.obsid, run_params.stokes_bins)
    batch_dir = "{}".format(os.path.join(comp_config['base_product_dir'], run_params.obsid, "batch"))
    job_id = submit_slurm(name, commands,\
                        batch_dir=batch_dir,\
                        slurm_kwargs={"time": "10:00:00"},\
                        module_list=["mwa_search/{0}".format(run_params.mwa_search),\
                                    "dspsr/master", "psrchive/master"],\
                        submit=True, vcstools_version=run_params.vcs_tools, mem="")

    logger.info("Job submitted using\n\
                pointing directory:         {0}\n\
                pulsar:                     {1}"\
                .format(run_params.pointing_dir, run_params.pulsar))

    return job_id

def submit_rm_cor_rvm(run_params, ipfb=False):
    """
    Runs the RM correction on the dspsr archive and writes the result to a text file.
    Relaunches the stokes_fold script afterwards for plotting

    Parameters:
    -----------
    run_params: object
        The run_params object from data_processing_pipeline.py
    """
    if ipfb:
        archive_name = "{0}_{1}_ipfb_archive.ar".format(run_params.obsid, run_params.pulsar)
        ascii_name = "{0}_{1}_ipfb_archive.txt".format(run_params.obsid, run_params.pulsar)
        rvm_name = "{0}_{1}_ipfb_RVM_fit.txt".format(run_params.obsid, run_params.pulsar)
        job_name = "ipfb_RMcor_RVM_{0}_{1}".format(run_params.pulsar, run_params.obsid)
    else:
        archive_name = "{0}_{1}_archive.ar".format(run_params.obsid, run_params.pulsar)
        ascii_name = "{0}_{1}_archive.txt".format(run_params.obsid, run_params.pulsar)
        rvm_name = "{0}_{1}_RVM_fit.txt".format(run_params.obsid, run_params.pulsar)
        job_name = "RMcor_RVM_{0}_{1}_{2}".format(run_params.pulsar, run_params.obsid, run_params.stokes_bins)
    rm_synth_files = glob.glob(os.path.join(run_params.pointing_dir, "*RMsynthesis*.txt".format(run_params.pulsar)))
    rm_fit_file = glob.glob(os.path.join(run_params.pointing_dir, "*{}*_rmfit.txt".format(run_params.pulsar)))[0]

    #Correct for RM
    if rm_synth_files:
        logger.info("Using RM synthesis result for correction")
        rm_synth_file = rm_synth_files[0]
        rm_dict = rm_synthesis.read_rmsynth_out(rm_synth_file)
        RM = rm_dict["0"]["rm"]
    elif rm_fit_file:
        logger.info("Using rmfit result for correction")
        RM = find_RM_from_file(rm_fit_file)[0]
    if not RM:
        RM = find_RM_from_cat(run_params.pulsar)[0]
    run_params.RM = RM
    try:
        commands = add_rm_cor_to_commands(run_params.pointing_dir, archive_name, run_params.RM, ascii_name=ascii_name)
    except ValueError:
        logger.info("RM could not be found from file or on ATNF for this pulsar. Cannot continue.")
        sys.exit(1)

    #RVM fitting
    commands = add_rvm_to_commands(run_params.pointing_dir, archive_name+"2", out_name=rvm_name, commands=commands, res=run_params.rvmres)

    #relaunch
    if not run_params.stop:
        launch_line = dpp.stokes_launch_line(run_params)
        commands.append(launch_line)

    batch_dir = "{0}{1}/batch/".format(comp_config['base_product_dir'], run_params.obsid)
    job_id = submit_slurm(job_name, commands,\
                        batch_dir=batch_dir,\
                        slurm_kwargs={"time": "12:00:00"},\
                        module_list=["mwa_search/{0}".format(run_params.mwa_search),
                                    "psrchive/master"],\
                        submit=True, vcstools_version=run_params.vcs_tools, mem="")

    return job_id

def work_out_what_to_do(run_params):
    """
    A logic structure that decides what to do next in the stokes_fold pipeline

    Parameters:
    -----------
    run_params: object
        The run_params object defined by data_procesing_pipeline
    """
    fits_files_in_dir = glob.glob(os.path.join(run_params.pointing_dir, "*.fits"))
    hdr_files_in_dir = glob.glob(os.path.join(run_params.pointing_dir, "*.hdr"))
    rm_fit_files = glob.glob(os.path.join(run_params.pointing_dir, "*{}*_rmfit.txt".format(run_params.pulsar)))
    rm_synth_files = glob.glob(os.path.join(run_params.pointing_dir, "*RM_synthesis*.txt"))
    rvm_fit_files = glob.glob(os.path.join(run_params.pointing_dir, "*{}*_RVM_fit.txt".format(run_params.pulsar)))
    ar_files = glob.glob(os.path.join(run_params.pointing_dir, "*{}*_archive.ar".format(run_params.pulsar)))
    ar2_files = glob.glob(os.path.join(run_params.pointing_dir, "*{}*_archive.ar2".format(run_params.pulsar)))
    chi_map_files = glob.glob(os.path.join(run_params.pointing_dir, "*chi_map*.txt"))

    rm_and_ar_exist = (bool(rm_fit_files) and bool(ar_files))
    ar2_and_rvm_exist = (bool(ar2_files) and bool(rvm_fit_files))
    ipfb = bool(hdr_files_in_dir)

    #Main logic structure
    if hdr_files_in_dir or fits_files_in_dir:
        if not rm_and_ar_exist:
            #Submit the fold and rmfit job
            if ipfb:
                submit_inverse_pfb_fold(run_params)
            else:
                submit_dspsr_rmfit(run_params)
            return

        elif rm_and_ar_exist and not ar2_and_rvm_exist:
            #Submit the rm correction and RVM fitting job
            submit_rm_cor_rvm(run_params, ipfb=ipfb)
            return

        elif ar2_and_rvm_exist:
            #get RVM dictionary + chi map
            try:
                rvm_dict = read_rvm_fit_file(rvm_fit_files[0])
                chi_map = read_chi_map(chi_map_files[0])
            except NotFoundError as e:
                rvm_dict = None
                chi_map = None
            #get RM
            if rm_synth_files:
                rm_dict = rm_synthesis.read_rmsynth_out(rm_synth_files[0])
                rm = rm_dict["0"]["rm"]
                rm_e = rm_dict["0"]["rm_e"]
            elif rm_fit_files:
                rm, rm_e = find_RM_from_file(rm_fit_files[0])
            if not rm:
                rm, rm_e = find_RM_from_cat(run_params.pulsar)
            #plot
            plot_everything(run_params.pulsar, run_params.obsid, run_params.pointing_dir, run_params.freq,\
                            rvm_dict=rvm_dict, chi_map=chi_map, rm=rm, rm_e=rm_e)
            return

        else:
            logger.error("Something has gone wrong trying to process the .vdif or .fits files :/")
            logger.info("Files found in pointing dir: {}".format(run_params.pointing_dir))
            logger.info("RM fit files: {}".format(rm_fit_files))
            logger.info("RVM fit files: {}".format(rvm_fit_files))
            logger.info("archive files: {}".format(ar_files))
            logger.info("RM corrected archive files: {}".format(ar2_files))
            return

    else:
        logger.error("No valid files in directory: {}".format(os.path.join(run_params.pointing_dir, "*.fits")))
        logger.debug("glob output: {}".format(fits_files_in_dir))
        return

if __name__ == '__main__':

    loglevels = dict(DEBUG=logging.DEBUG,
                     INFO=logging.INFO,
                     WARNING=logging.WARNING,
                     ERROR = logging.ERROR)

    parser = argparse.ArgumentParser(description="""Folds across stokes IQUV and attempts to find the RM""",\
                                    formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    foldop = parser.add_argument_group("Folding Options:")
    foldop.add_argument("-d", "--pointing_dir", type=str, help="Pointing directory that contains the fits files")
    foldop.add_argument("-p", "--pulsar", type=str, default=None, help="The J name of the pulsar.")
    foldop.add_argument("-b", "--nbins", type=int, default=0, help="The number of bins to fold the profile with")
    foldop.add_argument("-s", "--subint", type=float, default=None, help="The length of the integrations in seconds")
    foldop.add_argument("-o", "--obsid", type=str, help="The obsid of the observation")
    foldop.add_argument("--beg", type=int, help="The beginning of the observation time in gps time")
    foldop.add_argument("--end", type=int, help="The end of the observation time in gps time")
    foldop.add_argument("--dm", type=float, default=None, help="The dispersion measure to fold around")
    foldop.add_argument("--period", type=float, default=None, help="The period to fold around in milliseconds")
    foldop.add_argument("--dspsr_ops", type=str, default="", help="Provide as a string in quotes any dspsr command you would like to use for folding.\
                        eg: '-D 50.0 -c 0.50625'")
    foldop.add_argument("--no_ephem", action="store_true", help="Use this tag to override the use of the epehemeris")
    foldop.add_argument("--cand", action="store_true", help="Use this tag if this is not a known pulsar")

    rvmop = parser.add_argument_group("RVM Fitting Options:")
    rvmop.add_argument("--rvmres", type=int, default=90, help="The number of degree samples to try for alpha and beta.")

    otherop = parser.add_argument_group("Other Options:")
    otherop.add_argument("-L", "--loglvl", type=str, default="INFO", help="Logger verbosity level. Default: INFO", choices=loglevels.keys())
    otherop.add_argument("--vcs_tools", type=str, default="master", help="The version of vcstools to use. Default: master")
    otherop.add_argument("--mwa_search", type=str, default="master", help="The version of mwa_search to use. Default: master")
    otherop.add_argument("-S", "--stop", action="store_true", help="Use this tag to stop processing data after the chose mode has finished its intended purpose")
    otherop.add_argument("-f", "--freq", type=float, help="The central frequency of the observation in MHz")

    args = parser.parse_args()

    logger.setLevel(loglevels[args.loglvl])
    ch = logging.StreamHandler()
    ch.setLevel(loglevels[args.loglvl])
    formatter = logging.Formatter('%(asctime)s  %(filename)s  %(name)s  %(lineno)-4d  %(levelname)-9s :: %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.propagate = False


    if args.cand:
        if not args.period or not args.dm:
            logger.error("Period or DM not supplied. These need to be supplied for candidate folds")
            sys.exit(1)
        if not args.no_ephem:
            logger.warn("no_ephem tag needs to be used for candidate folds, but is turned off. Overriding.")
            args.no_ephem = True
        rounded_period = round(args.period, 5)
        rounded_dm = round(args.dm, 4)
        args.pulsar="cand_{0}_{1}".format(rounded_period, rounded_dm)

    else:
        if not args.beg or not args.end:
            logger.error("Beginning/end times not supplied. Please run again and specify times")
            sys.exit(1)

    if not args.pointing_dir:
        logger.error("Pointing directory not supplied. Please run again and specify a pointing directory")
        sys.exit(1)
    if not args.obsid:
        logger.error("Obsid not supplied. Please run again and specify an observation ID")
        sys.exit(1)
    if not args.pulsar:
        logger.error("Pulsar name not supplied. Please run again and specify puslar name")
        sys.exit(1)
    if args.no_ephem and (args.period is None or args.dm is None):
        logger.error("If no ephemeris is used, period and DM must be supplied")
        sys.exit(1)

    rp={}
    rp["pointing_dir"]      = args.pointing_dir
    rp["pulsar"]            = args.pulsar
    rp["obsid"]             = args.obsid
    rp["stop"]              = args.stop
    rp["mwa_search"]        = args.mwa_search
    rp["vcs_tools"]         = args.vcs_tools
    rp["loglvl"]            = args.loglvl
    rp["stokes_bins"]       = args.nbins
    rp["subint"]            = args.subint
    rp["beg"]               = args.beg
    rp["end"]               = args.end
    rp["freq"]              = args.freq
    rp["dspsr_ops"]         = args.dspsr_ops
    rp["no_ephem"]          = args.no_ephem
    rp["dm"]                = args.dm
    rp["period"]            = args.period
    rp["cand"]              = args.cand
    rp["rvmres"]            = args.rvmres
    run_params = dpp.run_params_class(**rp)

    work_out_what_to_do(run_params)