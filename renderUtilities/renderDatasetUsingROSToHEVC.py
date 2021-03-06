#!/usr/bin/env python3

# Winter Guerra <winterg@mit.edu>
# August 3nd, 2018

import fire
import glob2
import glob
import os
import sys
import re
import yaml
import csv
import shutil
import subprocess
import re
import numpy as np
import time
import threading
from pathos.multiprocessing import Pool

import importlib
from compressLosslessVideo import *

defaultPlaybackRate = 0.2  # For 2x RGBD feeds

#defaultTrajectoryList = [ "egg", "sphinx", "halfMoon", "oval", "ampersand", "dice", "bentDice", "thrice", "tiltedThrice", "winter", "clover", "mouse", "patrick", "picasso", "sid", "star", "cameraCalibration"]
defaultTrajectoryList = ["egg", "sphinx", "halfMoon", "oval", "ampersand", "dice", "bentDice",
                         "thrice", "tiltedThrice", "winter", "clover", "mouse", "patrick", "picasso", "sid", "star"]


def bagInWhitelist(bag, whitelist):
    returnCode = False
    if (whitelist):
        for f in whitelist:
            returnCode = returnCode or (bag in f)
    else:
        returnCode = True
    return returnCode


def runRendersOnDataset(datasetFolder, renderDir, renderPrefix, trajectoryFolders=defaultTrajectoryList, experimentList=[], bagfileWhitelistFile=None):

    devnull = open(os.devnull, 'wb')  # python >= 2.4

    config = yaml.safe_load(
        open(os.path.join(datasetFolder, "trajectoryOffsets.yaml"), 'r'))

    if (bagfileWhitelistFile):
        bagfileWhitelist = [ line.strip("\n") for line in open(bagfileWhitelistFile, 'r').readlines()]

    print(bagfileWhitelist)
    # Only select folders that we have offsets for
    # print config["unitySettings"]
#    trajectoryFolders = [ traj for traj in config["unitySettings"].keys()]

    print("Rendering the following trajectories: ")
    print(trajectoryFolders)

    for trajectoryFolder in trajectoryFolders:

        # iterate through trajectory environments
        for experiment in config["unitySettings"][trajectoryFolder]:

            if (len(experimentList) is 0 or experiment["name"] in experimentList):

                # Get universal render offset and convert to values compatible with TF
                renderOffsetArray = -1 * np.array(experiment["offset"])
                renderOffsetTranslation = renderOffsetArray[:3]
                theta = renderOffsetArray[3]
                renderOffsetRotation = np.array(
                    [0, 0, np.sin(theta*np.pi/360.0), np.cos(theta*np.pi/360.0)])

                # Find all '*.bag' files in folder
                bagFiles = glob2.glob(os.path.join(
                    datasetFolder, trajectoryFolder, '**/*.bag'))

                # Check that this experiment is applicable to this particular subset of logs
                subsetConstraint = experiment.get("yawDirectionConstraint", "")
                bagFiles = [f for f in bagFiles if subsetConstraint in f]
                #print(bagFiles)

                # Check that this bagfile is on the whitelist
                # Whitelist is of form: /BlackbirdDatasetData/oval/yawConstant/maxSpeed4p0/Small_Apartment
                # Bagfile is of form: /BlackbirdDatasetData/oval/yawConstant/maxSpeed4p0/rosbag.bag

                # Render these trajectories
                for bagFile in bagFiles:

                    bagPathStem = bagFile[:-4]
                    bagDirectory = os.path.dirname(bagFile)
                    (traj, yawType, speed) = bagDirectory.split('/')[-3:]

                    # Get per-trajectory centering offset.
                    offsetPath = os.path.join(
                        bagDirectory, "flightNormalizationOffset.csv")
                    trajectoryOffsetArray = np.loadtxt(
                        offsetPath, delimiter=',')
                    trajectoryOffsetString = " ".join(
                        map(str, trajectoryOffsetArray)) + " 0 0 0 1"

                    nameToCheck = os.path.join(traj,yawType,speed,experiment['name'])
                    print(nameToCheck)
                    if (not bagInWhitelist(nameToCheck, bagfileWhitelist)):
                        continue

                    print("========================================")
                    print(("Starting rendering of: " + bagFile))

                    # Clean output directory
                    outputDir = "{}_{}_{}".format(
                        bagPathStem, experiment["name"], renderPrefix)
                    try:
                        shutil.rmtree(renderDir)
                    except:
                        pass
                    try:
                        shutil.rmtree(outputDir)
                    except:
                        pass

                    os.mkdir(outputDir)
                    os.mkdir(renderDir)

                    # Generate timestamp file based on timestamp files left from the ISER dataset (120hz).
                    # NOTE: This file might not exist if the rosbag file is new (Egg trajectories).

                    ISERDataset120HzTimestamps = np.genfromtxt(os.path.join(
                        bagDirectory, "csv", "camera_l_slash_camera_info.csv"), delimiter=',', skip_header=1, usecols=[0], dtype=np.uint64)

                    timestampFile = os.path.join(
                        renderDir, "timestampsToRender.csv")
                    np.savetxt(timestampFile, ISERDataset120HzTimestamps,
                               delimiter=",", fmt='%u')
                    np.savetxt(os.path.join(outputDir, "120hzTimestamps.csv"),
                               ISERDataset120HzTimestamps, delimiter=",", fmt='%u')

                    # Adjust playback rate to be slower if in the NYC_Subway, because raycasting is slow in that environment
                    adjustedPlaybackRate = defaultPlaybackRate / \
                        2.0 if (experiment["environment"] in [
                                "NYC_Subway", "NYC_Subway_Moving_Train"]) else defaultPlaybackRate
                    # Run render command
                    #command = "roslaunch flightgoggles blackbirdDataset.launch bagfile_path:='{}' output_folder:='{}' framerate:='120' scene_filename:='{}' trajectory_offset_transform:='{}' render_offset_rotation:='{}' render_offset_translation:='{}'".format(bagFile, renderDir, experiment["environment"], trajectoryOffsetString, " ".join(map(str,renderOffsetRotation)), " ".join(map(str,renderOffsetTranslation)))

                    command = "roslaunch flightgoggles blackbirdDataset.launch bagfile_path:='{}' output_folder:='{}' timestampfile_path:='{}' scene_filename:='{}' scene_scale:='{}' playback_rate:='{}' trajectory_offset_transform:='{}' render_offset_rotation:='{}' render_offset_translation:='{}'".format(
                        bagFile, renderDir, timestampFile, experiment["environment"], experiment.get("scene_scale", 1.0), adjustedPlaybackRate, trajectoryOffsetString, " ".join(map(str, renderOffsetRotation)), " ".join(map(str, renderOffsetTranslation)))

                    print(command)

                    # , stdout=devnull)
                    process = subprocess.Popen(command, shell=True)
                    process.wait()

                    # Loop through output folders and compress files (in parallel).
                    def compressVideo(_folder_name):
                        subfolder_name = _folder_name.split('/')[-2]
                        print(subfolder_name)

                        # Compress files and move to final destination
                        if ("Segmented" in subfolder_name or "Depth" in subfolder_name):
                            print("Compressing image feed to PNG tarball")
                            compressVideoTarball(
                                _folder_name, ".ppm", output_folder=os.path.join(outputDir, subfolder_name))
                        else:
                            print("Compressing RGB/Gray image feed to lossless HEVC")
                            compressLosslessVideo(
                                _folder_name, ".ppm", output_folder=os.path.join(outputDir, subfolder_name))

                    #p = Pool()
                    #map(compressVideo, glob.glob(os.path.join(renderDir, "*/")))

                    for d in glob.glob(os.path.join(renderDir, "*/")):
                        compressVideo(d)

                    print(("Finished rendering of: " + bagFile))

                    time.sleep(2)


if __name__ == '__main__':
    fire.Fire(runRendersOnDataset)
    # fire.Fire(runRenderOnCSV)
