"""
For argument parsing and CLI interface
"""
import argparse
from . import Orchestrator
import yaml

def ConfType(nfields, nMax=None):
    """
    Returns an argument handler which expects the given number of fields
    Less than the specified number results in an error
    More than the specified number results in the extra fields being combined together
    """
    if nMax is None:
        nMax=nfields

    def parse_arg(argument):
        args = argument.split(':')
        if len(args) < nfields:
            raise argparse.ArgumentError(argument, "Not enough fields in argument (expected {}, got {})".format(nfields, len(args)))
        elif len(args) > nMax:
            args = args[:nMax-1] + ':'.join(args[nMax-1:])
        return args

    return parse_arg

def main():
    parser = argparse.ArgumentParser(
        'canine',
        description="A dalmatian-based job manager to schedule tasks using SLURM"
    )
    parser.add_argument(
        'pipeline',
        nargs='?',
        type=argparse.FileType('r'),
        help="Path to a pipeline file. Command line arguments will merge with,"
        "and override options in the file",
        default=None
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="If provided, the job will not actually run. Canine will parse job"
        " inputs and walk through localization, but will not ever schedule the job."
        " All inputs and job scripts will be prepared and localized in the staging"
        " directory"
    )
    parser.add_argument(
        '--export',
        type=argparse.FileType('w'),
        help="If provided, Canine will write the final merged pipeline object to"
        " the provided filepath",
        default=None
    )
    parser.add_argument(
        '-n', '--name',
        help="Name of the job",
        default=None
    )
    parser.add_argument(
        '-s', '--script',
        help="Path to the script to run",
        default=None,
        type=argparse.FileType('r')
    ),
    parser.add_argument(
        '-i', '--input',
        help="Script inputs. Must specify in the form inputName:inputValue. --input"
        " may be specified as many times as necessary, and inputNames may also be repeated",
        type=ConfType(2),
        action='append',
        default=[]
    )
    parser.add_argument(
        '-r', '--resources',
        help="SLURM arguments. Must specify in the form argName:argValue. --resources"
        " may be specified as many times as necessary, and a specific argName may also"
        " be repeated. Specify slurm arguments without leading dashes, but otherwise"
        " exactly as they'd appear on the command line. For slurm options"
        " which take no arguments, set --resources argName:true",
        type=ConfType(2),
        action='append',
        default=[]
    )
    parser.add_argument(
        '-a', '--adapter',
        help="Adapter configuration. Must specify in the form optionName:optionValue."
        " --adapter may be specified as many times as necessary",
        type=ConfType(2),
        action='append',
        default=[]
    )
    parser.add_argument(
        '-b', '--backend',
        help="Backend configuration. Must specify in the form optionName:optionValue."
        " --backend may be provided as many times as necessary",
        type=ConfType(2),
        action='append',
        default=[]
    )
    parser.add_argument(
        '-l', '--localization',
        help="Localization configuration. Must specify in the form optionName:optionValue."
        " --localization may be provided as many times as necessary. localization"
        " overrides should be specified using --localization overrides:outputName:overrideValue",
        type=ConfType(2,3),
        action='append',
        default=[]
    )
    parser.add_argument(
        '-o', '--output',
        help="Output patterns. Must specify in the form outputName:globPattern."
        " --output may be provided as many times as necessary.",
        type=ConfType(2),
        action='append',
        default=[]
    )
    args = parser.parse_args()
    conf = {}
    if args.pipeline:
        conf = yaml.load(args.pipeline, Loader=yaml.loader.SafeLoader)
    if args.name is not None:
        conf['name'] = args.name
    if args.script is not None:
        conf['script'] = args.script.name
    if len(args.resources) > 0:
        if 'resources' not in conf:
            conf['resources'] = {}
        conf['resources'] = {
            **conf['resources'],
            **{key: val for key, val in args.resources}
        }
    if len(args.adapter) > 0:
        print(args.adapter)
        if 'adapter' not in conf:
            conf['adapter'] = {}
        conf['adapter'] = {
            **conf['adapter'],
            **{key: val for key, val in args.adapter}
        }
    if len(args.backend) > 0:
        if 'backend' not in conf:
            conf['backend'] = {}
        conf['backend'] = {
            **conf['backend'],
            **{key: val for key, val in args.backend}
        }
    if len(args.output) > 0:
        if 'outputs' not in conf:
            conf['outputs'] = {}
            conf['outputs'] = {
                **conf['outputs'],
                **{key: val for key, val in args.output}
            }
    if len(args.input) > 0:
        inputs = {}
        for name, val in args.input:
            if name in inputs:
                if isinstance(inputs[name], list):
                    inputs[name].append(val)
                else:
                    inputs[name] = [inputs[name], val]
            else:
                inputs[name] = val
        if 'inputs' not in conf:
            conf['inputs'] = {}
        conf['inputs'] = {
            **conf['inputs'],
            **inputs
        }
    if len(args.localization) > 0:
        overrides = {}
        localization = {}
        for entry in args.localization:
            if entry[0] == 'overrides':
                overrides[entry[1]] = entry[2]
            else:
                localization[entry[0]] = entry[1]
        if 'localization' not in conf:
            conf['localization'] = {'overrides':{}}
        conf['localization'] = {
            **conf['localization'],
            **localization
        }
        if len(overrides):
            if 'overrides' not in conf['localization']:
                conf['localization']['overrides'] = {}
            conf['localization']['overrides'] = {
                **conf['localization']['overrides'],
                **overrides
            }
    if args.export is not None:
        yaml.dump(conf, args.export)
    Orchestrator(conf).run_pipeline(args.dry_run)

if __name__ == '__main__':
    main()
