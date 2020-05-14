nextflow.preview.dsl = 2

params.obsid = 1253471952
params.fitsdir = "/group/mwaops/vcs/${params.obsid}/pointings"
params.out_dir = "${params.search_dir}/${params.obsid}_candidates"

params.vcstools_version = 'master'
params.mwa_search_version = 'master'

params.begin = 0
params.end = 0
params.all = false

params.dm_min = 1
params.dm_max = 250

//Defaults for the accelsearch command
params.nharm = 16 // number of harmonics to search
params.min_period = 0.001 // min period to search for in sec (ANTF min = 0.0013)
params.max_period = 30 // max period to search for in sec  (ANTF max = 23.5)

//Some math for the accelsearch command
//convert to freq
min_freq = 1 / params.max_period
max_freq = 1 / params.min_period
//adjust the freq to include the harmonics
min_f_harm = min_freq
max_f_harm = max_freq * params.nharm

// Work out total obs time
if ( params.all ) {
    // an estimation since there's no easy way to make this work
    obs_length = 4805
}
else {
    obs_length = params.end - params.begin + 1
}

// Work out some estimated job times
if ( "$HOSTNAME".startsWith("galaxy") ) {
    search_dd_fft_acc_dur = '4h'
    prepfold_dur = '2h'
}
else{
    search_dd_fft_acc_dur = obs_length * 40.0
    prepfold_dur = obs_length * 1.5
}

process ddplan {
    label 'ddplan'

    input:
    tuple val(name), val(fits_files) //fits_files is actauly files but I assume this will save me link

    output:
    file 'DDplan.txt'
    
    """
    #!/usr/bin/env python3

    import find_pulsar_in_obs as fpio
    from lfDDplan import dd_plan
    import csv
    
    #obsid_pointing = "${fits_files[0]}".split("/")[-1].split("_ch")[0]
    #print(obsid_pointing)

    if '$name'.startswith('Blind'):
        output = dd_plan(150., 30.72, 3072, 0.1, $params.dm_min, $params.dm_max)
    else:
        if '$name'.startswith('FRB'):
            dm = fpio.grab_source_alog(source_type='FRB',
                 pulsar_list=['$name'.split("_")[0]], include_dm=True)[0][-1]
        else:
            # Try RRAT first
            rrat_temp = fpio.grab_source_alog(source_type='RRATs',
                        pulsar_list=['$name'.split("_")[0]], include_dm=True)
            if len(rrat_temp) == 0:
                #No RRAT so must be pulsar
                dm = fpio.grab_source_alog(source_type='Pulsar',
                     pulsar_list=['$name'.split("_")[0]], include_dm=True)[0][-1]
            else:
                dm = rrat_temp[0][-1]
        dm_min = float(dm) - 2.0
        if dm_min < 1.0:
            dm_min = 1.0
        dm_max = float(dm) + 2.0
        output = dd_plan(150., 30.72, 3072, 0.1, dm_min, dm_max)
    with open("DDplan.txt", "w") as outfile:
        spamwriter = csv.writer(outfile, delimiter=',')
        for o in output:
            spamwriter.writerow(['${name}'] + o)
    """ 
}


process search_dd_fft_acc {
    if ( "$HOSTNAME".startsWith("farnarkle") ) {
        scratch '$JOBFS'
        clusterOptions "--tmp=100GB"
    }
    else {
        container = "nickswainston/presto"
        //stageInMode = 'copy'
    }
    label 'cpu'
    time "${search_dd_fft_acc_dur}s"
    //Will ignore errors for now because I have no idea why it dies sometimes
    errorStrategy 'ignore'

    input:
    tuple val(name), val(dm_values), file(fits_files), val(chan)

    output:
    tuple val(name), file("*ACCEL_0"), file("*.inf"), file("*.singlepulse")
    //file "*ACCEL_0" optional true
    //Will have to change the ACCEL_0 if I do an accelsearch

    if ( "$HOSTNAME".startsWith("galaxy") ) {
        beforeScript "module load singularity/${params.singularity_module}"
    }
    else {
        beforeScript "module use ${params.presto_module_dir}; module load presto/${params.presto_module};"+\
                     "module load python/2.7.14; module load matplotlib/2.2.2-python-2.7.14;"+\
                     "module use $params.module_dir; module load mwa_search/py2_scripts"
    }


    """
    echo "lowdm highdm dmstep ndms timeres downsamp"
    echo ${dm_values}
    nsub=\$(calc_nsub.py -f ${(Float.valueOf(chan[0]) + Float.valueOf(chan[-1]))/2*1.28} -dm ${dm_values[1]})
    printf "\\n#Dedispersing the time series at \$(date +"%Y-%m-%d_%H:%m:%S") --------------------------------------------\\n"
    prepsubband -ncpus $task.cpus -lodm ${dm_values[0]} -dmstep ${dm_values[2]} -numdms ${dm_values[3]} -zerodm -nsub \$nsub -numout ${obs_length*10000} -o ${name} ${params.obsid}_*.fits
    printf "\\n#Performing the FFTs at \$(date +"%Y-%m-%d_%H:%m:%S") -----------------------------------------------------\\n"
    for i in \$(ls *.dat); do
        realfft \${i}
    done
    printf "\\n#Performing the periodic search at \$(date +"%Y-%m-%d_%H:%m:%S") ------------------------------------------\\n"
    for i in \$(ls *.dat); do
        accelsearch -ncpus $task.cpus -zmax 0 -flo $min_f_harm -fhi $max_f_harm -numharm $params.nharm \${i%.dat}.fft
    done
    single_pulse_search.py -p *.dat
    printf "\\n#Finished at \$(date +"%Y-%m-%d_%H:%m:%S") ----------------------------------------------------------------\\n"
    """
}


process accelsift {
    if ( "$HOSTNAME".startsWith("galaxy") ) {
        container = "nickswainston/presto"
        //stageInMode = 'copy'
    }
    label 'cpu'
    time '10m'
    publishDir params.out_dir, pattern: "*_singlepulse.tar.gz"
    publishDir params.out_dir, pattern: "*_singlepulse.ps"

    input:
    tuple val(name), file(accel_inf_single_pulse)

    output:
    tuple val(name), file("cands_*greped.txt"), file("*_singlepulse.tar.gz"), file("*_singlepulse.ps")

    if ( "$HOSTNAME".startsWith("galaxy") ) {
        beforeScript "module load singularity/${params.singularity_module}"
    }
    else {
        beforeScript "module use ${params.presto_module_dir}; module load presto/${params.presto_module};"+\
                     "module load python/2.7.14; module load matplotlib/2.2.2-python-2.7.14;"+\
                     "module use $params.module_dir; module load mwa_search/py2_scripts"
    }

    """
    ACCEL_sift.py --file_name ${name}
    if [ -f cands_${name}.txt ]; then
        grep ${name} cands_${name}.txt > cands_${name}_greped.txt
    else
        #No candidates so make an empty file
        touch cands_${name}_greped.txt
    fi
    single_pulse_search.py *.singlepulse
    tar -czvf singlepulse.tar.gz *DM*.singlepulse
    mv singlepulse.tar.gz ${name}_singlepulse.tar.gz
    """
}


process prepfold {
    label 'cpu'
    time "${prepfold_dur}s"

    input:
    tuple file(fits_files), val(cand_line)

    output:
    file "*pfd*"

    beforeScript "module use ${params.presto_module_dir}; module load presto/${params.presto_module}"

    //no mask command currently
    """
    echo "${cand_line.split()}"
    # Set up the prepfold options to match the ML candidate profiler
    period=${Float.valueOf(cand_line.split()[7])/1000}
    if (( \$(echo "\$period > 0.01" | bc -l) )); then
        nbins=100
        ntimechunk=120
        dmstep=1
        period_search_n=1
    else
        # bin size is smaller than time resolution so reduce nbins
        nbins=50
        ntimechunk=40
        dmstep=3
        period_search_n=2
    fi

    prepfold  -o ${cand_line.split()[0]} \
-n \$nbins -noxwin -noclip -p \$period -dm ${cand_line.split()[1]} -nsub 256 -npart \$ntimechunk \
-dmstep \$dmstep -pstep 1 -pdstep 2 -npfact \$period_search_n -ndmfact 1 -runavg *.fits
    """
}


process search_dd {
    if ( "$HOSTNAME".startsWith("farnarkle") ) {
        scratch '$JOBFS'
        clusterOptions "--tmp=100GB"
    }
    else {
        container = "nickswainston/presto"
        //stageInMode = 'copy'
    }
    label 'cpu'
    time '4h'
    //Will ignore errors for now because I have no idea why it dies sometimes
    errorStrategy 'ignore'

    input:
    tuple val(name), val(dm_values), file(fits_files), val(chan)

    output:
    tuple val(name), file("*.inf"), file("*.singlepulse")
    //Will have to change the ACCEL_0 if I do an accelsearch

    if ( "$HOSTNAME".startsWith("galaxy") ) {
        beforeScript "module load singularity/${params.singularity_module}"
    }
    else {
        beforeScript "module use ${params.presto_module_dir}; module load presto/${params.presto_module};"+\
                     "module load python/2.7.14; module load matplotlib/2.2.2-python-2.7.14;"+\
                     "module use $params.module_dir; module load mwa_search/py2_scripts"
    }

    """
    echo "lowdm highdm dmstep ndms timeres downsamp"
    echo ${dm_values}
    nsub=\$(calc_nsub.py -f ${(Float.valueOf(chan[0]) + Float.valueOf(chan[-1]))/2*1.28} -dm ${dm_values[1]})
    printf "\\n#Dedispersing the time series at \$(date +"%Y-%m-%d_%H:%m:%S") --------------------------------------------\\n"
    prepsubband -ncpus $task.cpus -lodm ${dm_values[0]} -dmstep ${dm_values[2]} -numdms ${dm_values[3]} -zerodm -nsub \$nsub -numout ${obs_length*10000} -o ${name} ${params.obsid}_*.fits
    single_pulse_search.py -p *.dat
    """
}


process assemble_single_pulse {
    if ( "$HOSTNAME".startsWith("galaxy") ) {
        container = "nickswainston/presto"
        //stageInMode = 'copy'
    }
    label 'cpu'
    time '10m'
    publishDir params.out_dir

    input:
    tuple val(name), file(inf_single_pulse)

    output:
    tuple val(name), file("*_singlepulse.tar.gz"), file("*_singlepulse.ps")

    if ( "$HOSTNAME".startsWith("galaxy") ) {
        beforeScript "module load singularity/${params.singularity_module}"
    }
    else {
        beforeScript "module use ${params.presto_module_dir}; module load presto/${params.presto_module};"+\
                     "module load python/2.7.14; module load matplotlib/2.2.2-python-2.7.14"
    }

    """
    single_pulse_search.py *.singlepulse
    tar -czvf singlepulse.tar.gz *DM*.singlepulse
    mv singlepulse.tar.gz ${name}_singlepulse.tar.gz
    """
}


workflow pulsar_search {
    take:
        name_fits_files // [val(candidateName_obsid_pointing), file(fits_files)]
        channels // channels from get_channels process
    main:
        ddplan( name_fits_files )
        search_dd_fft_acc( // combine the fits files and ddplan witht he matching name key (candidateName_obsid_pointing)
                           ddplan.out.splitCsv().map{ it -> [ it[0], [ it[1], it[2], it[3], it[4], it[5], it[6] ] ] }.concat(name_fits_files).groupTuple().\
                           // Find for each ddplan match that with the fits files and the name key
                           map{ it -> [it[1].init(), [[it[0], it[1].last()]]].combinations() }.flatMap().\
                           // Put channels on the end of the tuple then change the format to [val(name), val(dm_values), file(fits_files), val(chan)]
                           combine(channels.map{ it -> [it]}).map{ it -> [it[1][0], it[0], it[1][1], it[2]]} )
        // Get all the inf, ACCEL and single pulse files and sort them into groups with the same name key
        accelsift( search_dd_fft_acc.out.map{ it -> [it[0], it[1] + it[2] + it[3]] }.groupTuple( size: 6 ).map{ it -> [it[0], it[1].flatten()]} )//
        // Make a pair of accelsift out lines and fits files that match
        prepfold( name_fits_files.join(accelsift.out[0].map{ it -> it[1] }.splitCsv().flatten().map{ it -> [it.split()[0].split("_DM")[0], it ] }).\
                  map{ it -> [it[1], it[2]] } )
    emit:
        accelsift.out 
        prepfold.out
}

workflow single_pulse_search {
    take:
        name_fits_files
        channels
    main:
        ddplan( name_fits_files )
        search_dd( // combine the fits files and ddplan witht he matching name key (candidateName_obsid_pointing)
                    ddplan.out.splitCsv().map{ it -> [ it[0], [ it[1], it[2], it[3], it[4], it[5], it[6] ] ] }.concat(name_fits_files).groupTuple().\
                    // Find for each ddplan match that with the fits files and the name key
                    map{ it -> [it[1].init(), [[it[0], it[1].last()]]].combinations() }.flatMap().\
                    // Put channels on the end of the tuple then change the format to [val(name), val(dm_values), file(fits_files), val(chan)]
                    combine(channels.map{ it -> [it]}).map{ it -> [it[1][0], it[0], it[1][1], it[2]]} )
        // Get all the inf and single pulse files and sort them into groups with the same basename (obsid_pointing)
        assemble_single_pulse( search_dd.out.map{ it -> [it[0], it[1] + it[2]] }.groupTuple( size: 6 ).map{ it -> [it[0], it[1].flatten()] } )
    emit:
        assemble_single_pulse.out
}