#!/usr/bin/env python

import argparse
import numpy as np
import os

def sqrt_sum_of_squares(input_array):
    tmp_sum = 0.
    for a in input_array:
        tmp_sum += a
    return np.sqrt(tmp_sum)


def sraight_line(x, m, c):
    return m*x + c


def read_beanchmark_jobs(max_pointing_num, file_dir):
    
    pn_average_total = []
    pn_average_total_std = []
    pn_average_read  = []
    pn_average_read_std  = []
    pn_average_calc  = []
    pn_average_calc_std  = []
    pn_average_write = []
    pn_average_write_std = []
    # loop over input files
    for pn in range(1, max_pointing_num + 1):
        temp_total_time = []
        temp_read_time  = []
        temp_read_time_std  = []
        temp_calc_time  = []
        temp_calc_time_std  = []
        temp_write_time = []
        temp_write_time_std = []
        for ch in range(1, 25):
            with open(os.path.join(file_dir, "make_beam_{:03d}_n{}_output.txt".format(ch, pn)), "r") as batch_file:
                lines = batch_file.readlines()
                for line in lines:
                    if "**FINISHED BEAMFORMING**" in line:
                        temp_total_time.append(float(line.split("]")[0][1:]))
                    elif "Mean  read  processing time" in line:
                        temp_read_time.append(float(line.split("time: ")[1].split("+\-")[0]))
                        temp_read_time_std.append(float(line.split("+\-")[1][:-3]))
                    elif "Mean  calc  processing time" in line:
                        temp_calc_time.append(float(line.split("time: ")[1].split("+\-")[0])*pn)
                        temp_calc_time_std.append(float(line.split("+\-")[1][:-3])*pn)
                    elif "Mean  write processing time" in line:
                        temp_write_time.append(float(line.split("time: ")[1].split("+\-")[0]))
                        temp_write_time_std.append(float(line.split("+\-")[1][:-3]))

        # Calculate the standard deviations for each number of pointings
        pn_average_total.append(np.mean(temp_total_time))
        pn_average_total_std.append(np.std(temp_total_time))
        
        pn_average_read.append(np.mean(temp_read_time))
        average_read_std = np.std(temp_read_time)
        temp_read_time_std.append(average_read_std)
        pn_average_read_std.append(sqrt_sum_of_squares(temp_read_time_std)/24)

        pn_average_calc.append(np.mean(temp_calc_time))
        average_calc_std = np.std(temp_calc_time)
        temp_calc_time_std.append(average_calc_std)
        pn_average_calc_std.append(sqrt_sum_of_squares(temp_calc_time_std)/24)

        pn_average_write.append(np.mean(temp_write_time))
        average_write_std = np.std(temp_write_time)
        temp_write_time_std.append(average_write_std)
        pn_average_write_std.append(sqrt_sum_of_squares(temp_write_time_std)/24)

    print(f"MPB Times: {pn_average_total}")
    print(f"MPB Times std: {pn_average_total_std}")

    print(f"MPB GPU Times: {pn_average_calc}")
    print(f"MPB GPU Times std: {pn_average_calc_std}")
    print("")

    # Single-pixel calc
    single_pixel_times = []
    for ch in range(1, 25):
        with open(os.path.join(file_dir, "make_beam_{:03d}_single-pixel_output.txt".format(ch, pn)), "r") as batch_file:
            lines = batch_file.readlines()
            for line in lines:
                if "**FINISHED BEAMFORMING**" in line:
                    single_pixel_times.append(float(line.split("]")[0][1:]))
    print("SPB Times: {}".format(np.mean(single_pixel_times)))
    print("SPB Times std: {}".format(np.std(single_pixel_times)))
    print("")

    # IPFB calc
    IPFB_times = []
    for ch in range(1, 25):
        with open(os.path.join(file_dir, "make_beam_{:03d}_IPFB_output.txt".format(ch, pn)), "r") as batch_file:
            lines = batch_file.readlines()
            for line in lines:
                if "**FINISHED BEAMFORMING**" in line:
                    IPFB_times.append(float(line.split("]")[0][1:]))
    print("IPFB Times: {}".format(np.mean(IPFB_times)))
    print("IPFB Times std: {}".format(np.std(IPFB_times)))

    if max_pointing_num > 1:
        # Work out the benchmarks to be put into nextflow.config
        from scipy.optimize import curve_fit
        popt, pcov = curve_fit(sraight_line, range(1, max_pointing_num + 1), pn_average_calc) # your data x, y to fit
        perr = np.sqrt(np.diag(pcov))
        cal = popt[1]
        cal_err = perr[1]
        beam = popt[0]
        beam_err = perr[0]
        print("Benchmarks in ms")
        print("bm_read  = {:5.1f} +\- {:5.1f} ms".format(np.mean(pn_average_read)*1000, np.mean(pn_average_read_std)*1000))
        print("bm_cal   = {:5.1f} +\- {:5.1f} ms".format(cal*1000, cal_err*1000))
        print("bm_beam  = {:5.1f} +\- {:5.1f} ms".format(beam*1000, beam_err*1000))
        print("bm_write = {:5.1f} +\- {:5.1f} ms".format(np.mean(pn_average_write)*1000, np.mean(pn_average_write_std)*1000))

        print("Benchmarks in s including std to be put into nextflow.config")
        print("params.bm_read  = {:6.3f}".format(np.mean(pn_average_read) + np.mean(pn_average_read_std)))
        print("params.bm_cal   = {:6.3f}".format(cal + cal_err))
        print("params.bm_beam  = {:6.3f}".format(beam + beam_err))
        print("params.bm_write = {:6.3f}".format(np.mean(pn_average_write) + np.mean(pn_average_write_std)))


    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="""Automate benchmarking""")
    parser.add_argument('--max_pointing_num', type=int, default=20,
            help="Max number of pointings for multipixel beamformer")
    parser.add_argument('-f', '--file_dir', type=str, default='./',
            help="Directory containging files containing the output of make_beam.")
    args = parser.parse_args()

    read_beanchmark_jobs(args.max_pointing_num, args.file_dir)