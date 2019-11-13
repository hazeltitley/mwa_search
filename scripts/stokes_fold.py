#!/usr/bin/env python3

import logging
import argparse
import sys
import os

from job_submit import submit_slurm
import config
import data_process_pipeline
import plotting_toolkit

import psrqpy
import numpy as np
logger = logging.getLogger(__name__)

#get ATNF db location
try:
    ATNF_LOC = os.environ['PSRCAT_FILE']
except:
    logger.warn("ATNF database could not be loaded on disk. This may lead to a connection failure")
    ATNF_LOC = None

def find_RM_from_cat(pulsar):
    #Tries to get RM from psrcat

    query = psrqpy.QueryATNF(params=["RM"], psrs=[pulsar], loadfromdb=ATNF_LOC).pandas 
    rm = query["RM"][0]
    rm_err = query["RM_ERR"][0]

    if np.isnan(rm):
        return None, None
    elif np.isnan(rm_err):
        rm_err = 0.15*rm
    return rm, rm_err


def find_RM_from_file(fname):
   #Tries to get RM from an rmfit output
    f = open(fname)
    lines=f.readlines()
    rm=None
    rm_err=None
    for line in lines:
        line = line.split()
        if line[0] == "Best" and line[1] == "RM":
            rm=float(line[3])
            rm_err=float(line[5])
            break

    if not rm:
        logger.warn("RM could not be generated from archive file")

    return rm, rm_err

#def submit_RM()
    #This is not implemented in the pulsar databse yet

def submit_dspsr(run_params):
    #run dspsr

    launch_line = "stokes_fold.py -m f -d {0} -p {1} -b {2} -s {3} -o {4} -L {5}\
                    --mwa_search {6} --vcs_tools {7}"\
                    .format(run_params.pointing_dir, run_params.pulsar, run_params.stokes_bins,\
                    run_params.subint, run_params.obsid, run_params.loglvl, run_params.mwa_search,\
                    run_params.vcs_tools)
    if run_params.stop==True:
        launch_line += " -S"


    commands=[]
    commands.append("psrcat -e {0} > {1}/{0}.eph".format(run_params.pulsar, run_params.pointing_dir))
    commands.append("echo 'Running DSPSR folding...\n'")
    commands.append("dspsr -cont -U 4000 -A -L {0} -E {3}/{1}.eph -K -b {2} -O {3}/{1}_subint_{0} {3}/*.fits"\
                    .format(run_params.subint, run_params.pulsar, run_params.stokes_bins, run_params.pointing_dir))
    commands.append("echo 'Attempting to find rotation measure.\nOutputting result to {0}/{1}_rmfit.txt\n'"\
                    .format(run_params.pointing_dir, run_params.pulsar))
    commands.append("rmfit {0}/{1}_subint_{2}.ar -t > {0}/{1}_rmfit.txt"\
                    .format(run_params.pointing_dir, run_params.pulsar, run_params.subint))

    #rerun the script
    commands.append(launch_line)

    name = "dspsr_RM_{0}_{1}".format(run_params.pulsar, run_params.obsid)
    comp_config = config.load_config_file()
    batch_dir = "{0}{1}/batch/".format(comp_config['base_product_dir'], run_params.obsid)
    submit_slurm(name, commands,\
                batch_dir=batch_dir,\
                slurm_kwargs={"time": "08:00:00"},\
                module_list=["mwa_search/{0}".format(run_params.mwa_search),\
                            "dspsr/master", "psrchive/master"],\
                submit=True, vcstools_version=run_params.vcs_tools)

    logger.info("Job submitted for dspsr using\n\
                pointing directory:         {0}\n\
                pulsar:                     {1}"\
                .format(run_params.pointing_dir, run_params.pulsar))

def submit_RM_correct(run_params):
    #correct for RM and submit plot


    launch_line = "stokes_fold.py -m p -d {0} -p {1} -b {2} -s {3} -o {4} -L {5}\
                    --mwa_search {6} --vcs_tools {7}"\
                    .format(run_params.pointing_dir, run_params.pulsar, run_params.stokes_bins,\
                    run_params.obsid, run_params.subint, run_params.loglvl,\
                    run_params.mwa_search, run_params.vcs_tools)

    if run_params.stop==True:
        launch_line += " -S"

    commands = []
    #correct for RM
    commands.append("echo 'Correcting for input rotation measure\n'")
    commands.append("pam -e ar2 -R {0} {1}/{2}_subint_{3}.ar"\
    .format(run_params.RM, run_params.pointing_dir, run_params.pulsar, run_params.subint))
    #Turn the archive into a readable ascii file
    commands.append("echo 'Wiritng result to text file\n'")
    commands.append("pdv -FTt {0}/{1}_subint_{2}.ar2 > {0}/{1}_archive.txt".\
    format(run_params.pointing_dir, run_params.pulsar, run_params.subint))

    #launch plotting
    commands.append(launch_line)

    name = "RMcor_plt_{0}_{1}".format(run_params.pulsar, run_params.obsid)
    comp_config = config.load_config_file()
    batch_dir = "{0}{1}/batch/".format(comp_config['base_product_dir'],
                                       run_params.obsid)
    submit_slurm(name, commands,\
                batch_dir=batch_dir,\
                slurm_kwargs={"time": "02:00:00"},\
                module_list=["mwa_search/{0}".format(run_params.mwa_search),
                            "psrchive/master"],\
                submit=True, vcstools_version=run_params.vcs_tools)


if __name__ == '__main__':
    #dictionary for choosing log-levels
    loglevels = dict(DEBUG=logging.DEBUG,
                     INFO=logging.INFO,
                     WARNING=logging.WARNING,
                     ERROR = logging.ERROR)

    #Arguments
    parser = argparse.ArgumentParser(description="""Folds across stokes IQUV and attempts to find the RM""")

    foldop = parser.add_argument_group("Folding Options:")
    foldop.add_argument("-d", "--pointing_dir", type=str, help="Pointing directory that contains the fits files")
    foldop.add_argument("-p", "--pulsar", type=str, default=None, help="The name of the pulsar. If not provided, the script will try to get the pulsar from the pointing directory")
    foldop.add_argument("-b", "--nbins", type=int, help="The number of bins to fold the profile with")
    foldop.add_argument("-s", "--subint", type=float, default=10.0, help="The length of the integrations in seconds. Default: 10.0")
    foldop.add_argument("-o", "--obsid", type=str, help="The obsid of the observation")

    otherop = parser.add_argument_group("Other Options:")
    otherop.add_argument("-L", "--loglvl", type=str, default="INFO", help="Logger verbosity level. Default: INFO", choices=loglevels.keys())
    otherop.add_argument("--vcs_tools", type=str, default="master", help="The version of vcstools to use. Default: master")
    otherop.add_argument("--mwa_search", type=str, default="master", help="The version of mwa_search to use. Default: master")
    otherop.add_argument("-S", "--stop", action="store_false", help="Use this tag to stop processing data after the chose mode has finished its intended purpose")

    modeop = parser.add_argument_group("Mode Options:")
    modeop.add_argument("-m", "--mode", type=str, help="The mode in which to run stokes_fold: \n\
                        'i' - Creates a dspsr archive and runs rmfit and outputs the result\n\
                        'f' - Reads an RMfit output and corrects the archive for RM. Then creates\
                        an ascii text file\n\
                        'p' - Plots a dspsr ascii text file")

    args = parser.parse_args()

    logger.setLevel(loglevels[args.loglvl])
    ch = logging.StreamHandler()
    ch.setLevel(loglevels[args.loglvl])
    formatter = logging.Formatter('%(asctime)s  %(filename)s  %(name)s  %(lineno)-4d  %(levelname)-9s :: %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.propagate = False
    
    if args.nbins is None:
        logger.error("Please input an argument for the number of bins to use")
        sys.exit(1)
    if args.pulsar is None:
        logger.error("Pulsar name not supplied. Please run again and specify puslar name")
        sys.exit(1)
    if args.pointing_dir is None:
        logger.error("Pointing directory not supplied. Please run again and specify a pointing directory")
        sys.exit(1)
    if args.obsid is None:
        logger.error("Obsid not supplied. Please run again and specify a pointing directory")
        sys.exit(1)

    run_params = data_process_pipeline.run_params_class(pointing_dir=args.pointing_dir,\
                pulsar=args.pulsar, stokes_bins=args.nbins, loglvl=args.loglvl, subint=args.subint,\
                mode=args.mode, vcs_tools=args.vcs_tools, mwa_search=args.mwa_search,\
                obsid=args.obsid, stop=args.stop)

    if run_params.mode == "i":
        submit_dspsr(run_params)

    elif run_params.mode == "f":
        RM, RM_err = find_RM_from_file("{0}/{1}_rmfit.txt".format(run_params.pointing_dir,\
        run_params.pulsar))
        if RM is None:
            RM, RM_err = find_RM_from_cat(run_params.pulsar)

        if RM is not None:
            logger.info("Submitting RM correction and plot script for RM: {0}".format(RM))
            run_params.set_RM_and_err(RM, RM_err)
            submit_RM_correct(run_params)
        else:
            logger.info("RM could not be generated and is not found on record. Cannot proceed")

    elif run_params.mode == "p":
        #make polarisation plot and copy data products to different directory
        fname="{0}/{1}_archive.txt".format(run_params.pointing_dir, run_params.pulsar)
        fig_name = plotting_toolkit.plot_archive(obsid=run_params.obsid, archive=fname, pulsar=run_params.pulsar, out_dir=run_params.pointing_dir)
        logger.info("Moving data products to '/group/mwaops/vcs/{0}/data_products/'".format(run_params.obsid))
        data_products_dir = "/group/mwaops/vcs/{0}/data_products/{1}".format(run_params.obsid, run_params.pulsar)
        data_process_pipeline.copy_data(fig_name, data_products_dir)
        data_process_pipeline.copy_data("{0}/{1}_rmfit.txt".format(run_params.pointing_dir, run_params.pulsar), data_products_dir)
        data_process_pipeline.copy_data("{0}/{1}_subint_{2}.ar".format(run_params.pointing_dir, run_params.pulsar, run_params.subint), data_products_dir)
        data_process_pipeline.copy_data("{0}/{1}_subint_{2}.ar2".format(run_params.pointing_dir, run_params.pulsar, run_params.subint), data_products_dir)
        data_process_pipeline.copy_data("{0}/{1}_archive.txt".format(run_params.pointing_dir, run_params.pulsar), data_products_dir)

    else:
        logger.error("Unrecognised mode. Pleasre rerun with a suitable mode selscted")
        sys.exit(1)
