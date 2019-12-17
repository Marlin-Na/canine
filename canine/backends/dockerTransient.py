# vim: set expandtab:

import typing
import subprocess
import os
import sys
import docker

from .imageTransient import TransientImageSlurmBackend, list_instances, gce
from ..utils import get_default_gcp_project, gcp_hourly_cost

import pandas as pd

class DockerTransientImageSlurmBackend(TransientImageSlurmBackend):
    def __init__(self, **kwargs):
        kwargs["worker_prefix"] = socket.gethostname()
        super().__init__(**kwargs)
        # <set image to latest in family> (obv. need to check if this exists first)

    def init_slurm(self):
        self.dkr = docker.from_env()

        #
        # check if image exists
        try:
            image = self.dkr.images.get('broadinstitute/pydpiper:latest')
        except docker.errors.ImageNotFound:
            raise Exception("You have not yet built or pulled the Slurm Docker image!")

        #
        # start the Slurm container if it's not already running
        #if image not in [x.image for x in self.dkr.containers.list()]:

    def start_NFS(self):
        # check if NFS server is already running
        zone_dict = gce.zones().list(project = self.config["project"]).execute()
        
        subprocess.check_call(
            """gcloud compute instances create {nfs_worker} \
               --image {image} --machine-type {worker_type} --zone {compute_zone} \
               {compute_script} {compute_script_file} {preemptible} \
               --tags caninetransientimage
            """.format(**self.config, workers = " ".join(nodes_to_create)),
            shell = True
        )

# Python version of checks in docker_run.sh
def ready_for_docker():
    #
    # check if Slurm/Munge are already running
    already_running = [["slurmctld", "A Slurm controller"],
                       ["slurmdbd", "The Slurm database daemon"],
                       ["munged", "Munge"]]

    for proc, desc in already_running:
        try:
            ret = subprocess.check_call(
              "pgrep {} &> /dev/null".format(proc),
              shell = True,
              executable = '/bin/bash'
            )
        except subprocess.CalledProcessError:
            ret = 1
        finally:
            if ret == 0:
                raise Exception("{desc} is already running on this machine. Please run `[sudo] killall {proc}' and try again.".format(desc = desc, proc = proc))

    #
    # check if mountpoint exists
    try:
        subprocess.check_call(
          "mountpoint -q /mnt/nfs".format(proc),
          shell = True,
          executable = '/bin/bash'
        )
    except subprocess.CalledProcessError:
        # TODO: add the repo URL
        raise Exception("NFS did not successfully mount. Please report this bug as a GitHub issue.")
