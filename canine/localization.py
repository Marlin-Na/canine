import os
import sys
import warnings
import typing
import fnmatch
import shlex
from subprocess import CalledProcessError
from uuid import uuid4
from collections import namedtuple
from contextlib import ExitStack
from .backends import AbstractSlurmBackend, AbstractTransport
from .utils import get_default_gcp_project, check_call

# * `CANINE`: The current canine version
# * `CANINE_BACKEND`: The name of the current backend type
# * `CANINE_ADAPTER`: The name of the current adapter type
# * `CANINE_ROOT`: The path to the staging directory
# * `CANINE_COMMON`: The path to the directory where common files are localized
# * `CANINE_OUTPUT`: The path to the directory where job outputs will be staged during delocalization
# * `CANINE_JOB_VARS`: A colon separated list of the names of all variables generated by job inputs
# * `CANINE_JOB_INPUTS`: The path to the directory where job inputs are localized
# * `CANINE_JOB_ROOT`: The path to the working directory for the job. Equal to CWD at the start of the job. Output files should be written here

# $CANINE_ROOT: staging dir
#   /common: common inputs
#   /outputs: output dir (unused?)
#       /{jobID}: job specific output
#   /jobs/{jobID}: job staging dir
#       setup.sh: setup script
#       script.sh: main script
#       teardown.sh: teardown script
#       /inputs: job specific input
#       /workspace: job starting cwd and output dir

Localization = namedtuple("Localization", ['type', 'path'])
# types: stream, download, None
# indicates what kind of action needs to be taken during job startup

class Localizer(object):
    """
    Class for handling file localization and delocalization
    Responsible for setting up staging directories and managing inputs and outputs
    NOT responsible for copying results to FireCloud (handled at the adapter layer)
    """
    requester_pays = {}

    def __init__(self, backend: AbstractSlurmBackend, localize_gs: bool = True, common: bool = True, staging_dir: str = None, mount_path: str = None):
        """
        Initializes the Localizer using the given transport.
        Localizer assumes that the SLURMBackend is connected and functional during
        the localizer's entire life cycle.
        If staging_dir is not provided, a random directory is chosen
        """
        self.backend = backend
        self.localize_gs = localize_gs
        self.common = common
        self.common_inputs = set()
        self.__sbcast = False
        if staging_dir == 'SBCAST':
            # FIXME: This doesn't actually do anything yet
            # If sbcast is true, then localization needs to use backend.sbcast to move files to the remote system
            # Not sure at all how delocalization would work
            self.__sbcast = True
            staging_dir = None
        self.staging_dir = staging_dir if staging_dir is not None else str(uuid4())
        with self.backend.transport() as transport:
            self.mount_path = transport.normpath(mount_path if mount_path is not None else self.staging_dir)
        self.inputs = {} # {jobId: {inputName: (handle type, handle value)}}

        # Paths relative to staging dir on controller
        self.env = {
            'CANINE_ROOT': self.staging_dir,
            'CANINE_COMMON': os.path.join(self.staging_dir, 'common'),
            'CANINE_OUTPUT': os.path.join(self.staging_dir, 'outputs'), #outputs/jobid/outputname/...files...
            'CANINE_JOBS': os.path.join(self.staging_dir, 'jobs'),
        }

        # Paths relative to mount dir on worker
        self.environment =  {
            'CANINE_ROOT': self.mount_path,
            'CANINE_COMMON': os.path.join(self.mount_path, 'common'),
            'CANINE_OUTPUT': os.path.join(self.mount_path, 'outputs'),
            'CANINE_JOBS': os.path.join(self.mount_path, 'jobs'),
        }

    def __enter__(self):
        """
        Sets up staging directory for the job
        """
        with self.backend.transport() as transport:
            if not transport.isdir(self.env['CANINE_ROOT']):
                transport.mkdir(self.env['CANINE_ROOT'])
            if not transport.isdir(self.env['CANINE_COMMON']):
                transport.mkdir(self.env['CANINE_COMMON'])
            if not transport.isdir(self.env['CANINE_OUTPUT']):
                transport.mkdir(self.env['CANINE_OUTPUT'])
            if not transport.isdir(self.env['CANINE_JOBS']):
                transport.mkdir(self.env['CANINE_JOBS'])
        return self

    def __exit__(self, *args):
        """
        Assumes outputs have already been delocalized.
        Removes the staging directory
        """
        if len([arg for arg in args if arg is not None]) == 0:
            # Only clean if we are exiting the context cleanly
            self.backend.invoke('rm -rf {}'.format(self.env['CANINE_ROOT']))

    def get_requester_pays(self, path: str) -> bool:
        """
        Returns True if the requested gs:// object or bucket resides in a
        requester pays bucket
        """
        # FIXME: this sucks
        if path.startswith('gs://'):
            path = path[5:]
        bucket = path.split('/')[0]
        if bucket not in self.requester_pays:
            command = 'gsutil ls gs://{}'.format(path)
            try:
                rc, sout, serr = self.backend.invoke(command)
                self.requester_pays[bucket] = len([line for line in serr.readlines() if b'requester pays bucket but no user project provided' in line]) >= 1
            except CalledProcessError:
                pass
        return bucket in self.requester_pays and self.requester_pays[bucket]

    def localize(self, inputs: typing.Dict[str, typing.Dict[str, str]], overrides: typing.Optional[typing.Dict[str, typing.Optional[str]]] = None):
        """
        Localizes all input files
        Inputs: {jobID: {inputName: inputValue}}
        Overrides: {inputName: handling}
        """
        if overrides is None:
            overrides = {}
        overrides = {k:v.lower() if isinstance(v, str) else None for k,v in overrides.items()}
        with self.backend.transport() as transport:
            if self.common:
                seen = set()
                for jobId, values in inputs.items():
                    for arg, path in values.items():
                        if path in seen and (arg not in overrides or overrides[arg] == 'common'):
                            self.common_inputs.add(path)
                        if arg in overrides and overrides[arg] == 'common':
                            self.common_inputs.add(path)
                        seen.add(path)
            common_dests = {}
            for path in self.common_inputs:
                if path.startswith('gs://') and self.localize_gs:
                    common_dests[path] = os.path.join(self.environment['CANINE_COMMON'], os.path.basename(path))
                    command = "gsutil {} cp {} {}".format(
                        '-u {}'.format(get_default_gcp_project()) if self.get_requester_pays(path) else '',
                        path,
                        os.path.join(self.env['CANINE_COMMON'], os.path.basename(path))
                    )
                    rc, sout, serr = self.backend.invoke(command)
                    check_call(command, rc, sout, serr)
                elif os.path.isfile(path):
                    common_dests[path] = os.path.join(self.environment['CANINE_COMMON'], os.path.basename(path))
                    transport.send(
                        path,
                        os.path.join(self.env['CANINE_COMMON'], os.path.basename(path))
                    )
                else:
                    print("Could not handle common file", path, file=sys.stderr)
            for jobId, data in inputs.items():
                workspace_path = os.path.join(self.env['CANINE_JOBS'], str(jobId), 'workspace')
                if not transport.isdir(workspace_path):
                    transport.makedirs(workspace_path)
                self.inputs[jobId] = {}
                for arg, value in data.items():
                    mode = overrides[arg] if arg in overrides else False
                    if mode is not False:
                        if mode == 'stream':
                            self.inputs[jobId][arg] = Localization('stream', value)
                        elif mode == 'localize':
                            self.inputs[jobId][arg] = Localization(
                                None,
                                self.localize_file(transport, jobId, arg, value)
                            )
                        elif mode == 'delayed':
                            if not value.startswith('gs://'):
                                print("Ignoring 'delayed' override for", arg, "with value", value, "and localizing now", file=sys.stderr)
                                self.inputs[jobId][arg] = Localization(
                                    None,
                                    self.localize_file(transport, jobId, arg, value)
                                )
                            else:
                                self.inputs[jobId][arg] = Localization(
                                    'download',
                                    #self.localize_file(transport, jobId, arg, value, True)
                                    value
                                )
                        elif mode is None:
                            self.inputs[jobId][arg] = Localization(None, value)
                    elif value in common_dests:
                        # common override already handled
                        # No localization needed, already copied
                        self.inputs[jobId][arg] = Localization(None, common_dests[value])
                    else:
                        self.inputs[jobId][arg] = Localization(
                            None,
                            (
                                self.localize_file(transport, jobId, arg, value)
                                if os.path.isfile(value) or value.startswith('gs://')
                                else value
                            )
                        )

    def localize_file(self, transport: AbstractTransport, jobId: str, name: str, value: str, delayed: bool = False) -> str:
        """
        Localizes an individual file.
        Expects the caller to have initialized the transport and entered its context
        Common and stream handling are taken care of externally. This function only runs
        for files which are set to localize for each job
        If delayed is True, only a path will be produced, but no localization will occur
        """
        # Relative to master node
        filepath = os.path.join(
            self.env['CANINE_JOBS'],
            str(jobId),
            'inputs',
            os.path.basename(value)
        )
        transport.makedirs(os.path.dirname(filepath))
        while transport.exists(filepath):
            root, ext = os.path.splitext(filepath)
            filepath = '{}._alt{}'.format(root, ext)
        if not delayed:
            if self.localize_gs and value.startswith('gs://'):
                command = "gsutil {} cp {} {}".format(
                    '-u {}'.format(get_default_gcp_project()) if self.get_requester_pays(value) else '',
                    value,
                    filepath
                )
                check_call(command, *self.backend.invoke(command))
            elif os.path.isfile(value):
                transport.send(
                    value,
                    filepath
                )
        # Relative to compute node
        return os.path.join(
            self.environment['CANINE_JOBS'],
            str(jobId),
            'inputs',
            os.path.basename(value)
        )

    # def startup_hook(self, jobId: str) -> typing.Optional[str]:
    #     """
    #     Checks if the Localizer has any additional commands to add to this job's
    #     startup script
    #     Returns bash text or None
    #     Mostly, this will correspond to final localization for input files
    #     **WARNING** This function modifies Localizer.inputs
    #     After calling it on a job, all of that job's inputs should have a localization type of None
    #     """
    #     raise NotImplementedError("TODO")

    def localize_job(self, jobId: str, setup_text: typing.Optional[str] = None, transport: typing.Optional[AbstractTransport] = None) -> str:
        """
        Does final localization of job script.
        Used when finally prepping a job for dispatch
        returns the path to the first script in the job's pipeline
        """
        if setup_text is None:
            setup_text = ''
        with ExitStack() as stack:
            if transport is None:
                transport = stack.enter_context(self.backend.transport())
            job_vars = []
            exports = []
            extra_tasks = [
                'if [[ -d $CANINE_JOB_INPUTS ]]; then cd $CANINE_JOB_INPUTS; fi'
            ]
            for key, val in self.inputs[jobId].items():
                if val.type == 'stream':
                    job_vars.append(shlex.quote(key))
                    dest = shlex.quote(self.localize_file(transport, jobId, key, val.path, True))
                    extra_tasks += [
                        'mkfifo {}'.format(dest),
                        "gsutil {} cat {} > {} &".format(
                            '-u {}'.format(shlex.quote(get_default_gcp_project())) if self.get_requester_pays(val.path) else '',
                            shlex.quote(val.path),
                            dest
                        )
                    ]
                    exports.append('export {}="{}"'.format(
                        key,
                        dest
                    ))
                elif val.type == 'download':
                    job_vars.append(shlex.quote(key))
                    dest = shlex.quote(self.localize_file(transport, jobId, key, val.path, True))
                    extra_tasks += [
                        "gsutil {} cp {} {}".format(
                            '-u {}'.format(shlex.quote(get_default_gcp_project())) if self.get_requester_pays(val.path) else '',
                            shlex.quote(val.path),
                            dest
                        )
                    ]
                    exports.append('export {}="{}"'.format(
                        key,
                        dest
                    ))
                elif val.type is None:
                    job_vars.append(shlex.quote(key))
                    exports.append('export {}="{}"'.format(
                        key,
                        shlex.quote(val.path)
                    ))
                else:
                    print("Unknown localization command:", val.type, "skipping", key, val.path, file=sys.stderr)
            script_path = os.path.join(self.env['CANINE_JOBS'], jobId, 'setup.sh')
            script = '\n'.join(
                line.rstrip()
                for line in [
                    '#!/bin/bash',
                    'export CANINE_JOB_VARS={}'.format(':'.join(job_vars)),
                    'export CANINE_JOB_INPUTS="{}"'.format(os.path.join(self.environment['CANINE_JOBS'], jobId, 'inputs')),
                    'export CANINE_JOB_ROOT="{}"'.format(os.path.join(self.environment['CANINE_JOBS'], jobId, 'workspace')),
                    'export CANINE_JOB_SETUP="{}"'.format(os.path.join(self.environment['CANINE_JOBS'], jobId, 'setup.sh')),
                    'export CANINE_JOB_TEARDOWN="{}"'.format(os.path.join(self.environment['CANINE_JOBS'], jobId, 'teardown.sh')),
                ] + exports + extra_tasks
            ) + '\ncd $CANINE_JOB_ROOT\n' + setup_text
            with transport.open(script_path, 'w') as w:
                w.write(script)
            transport.chmod(script_path, 0o775)
            return  os.path.join(self.environment['CANINE_JOBS'], jobId, 'setup.sh')

    def delocalize(self, patterns: typing.Dict[str, str], output_dir: str = 'canine_output', jobId: typing.Optional[str] = None, transport: typing.Optional[AbstractTransport] = None, delete: bool = True) -> typing.Dict[str, typing.Dict[str, str]]:
        """
        Delocalizes the requested files from the given job (or all jobs)
        Returns a dict {jobId: {output name: output path}}
        """
        with ExitStack() as stack:
            if transport is None:
                transport = stack.enter_context(self.backend.transport())
            if jobId is None:
                return {
                    job: self.delocalize(patterns, output_dir, job, transport, delete)[job]
                    for job in transport.listdir(self.env['CANINE_JOBS'])
                }

            else:
                output_files = {}
                start_dir = os.path.join(self.env['CANINE_JOBS'], jobId, 'workspace')
                for dirpath, dirnames, filenames in transport.walk(start_dir):
                    for name, pattern in patterns.items():
                        for filename in filenames:
                            fullname = os.path.join(dirpath, filename)
                            if fnmatch.fnmatch(fullname, pattern) or fnmatch.fnmatch(os.path.relpath(fullname, start_dir), pattern):
                                output_files[name] = self.delocalize_file(transport, jobId, name, fullname, output_dir)
                                if delete:
                                    if transport.isfile(fullname):
                                        transport.remove(fullname)
                return {jobId: output_files}

    def delocalize_file(self, transport: AbstractTransport, jobId: str, name: str, fullname: str, output_dir: str) -> str:
        """
        Copies the remote output file back to the current filesystem
        """
        output_path = os.path.join(
            output_dir,
            jobId,
            name,
            os.path.basename(fullname)
        )
        os.makedirs(os.path.dirname(output_path))
        transport.receive(
            fullname,
            output_path
        )
        return output_path
