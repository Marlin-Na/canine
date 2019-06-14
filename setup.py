from setuptools import setup
import re
import os

with open(os.path.join(os.path.dirname(__file__), 'canine', 'orchestrator.py')) as r:
    version = re.search(r'version = \'(\d+\.\d+\.\d+[-_a-zA-Z0-9]*)\'', r.read()).group(1)

setup(
    name = 'canine',
    version = version,
    packages = [
        'canine',
        'canine.backends',
        'canine.adapters'
    ],
    package_data={
        '':[
            'backends/slurm-gcp/*',
            'backends/slurm-gcp/scripts/*'
        ],
    },
    description = 'A dalmatian-based job manager to schedule tasks using SLURM',
    url = 'https://github.com/broadinstitute/canine',
    author = 'Aaron Graubert - Broad Institute - Cancer Genome Computational Analysis',
    author_email = 'aarong@broadinstitute.org',
    long_description = "A dalmatian-based job manager to schedule tasks using SLURM",
    # long_description_content_type = 'text/markdown',
    install_requires = [
        'paramiko>=2.5.0',
        'pandas>=0.24.1',
        'google-auth>=1.6.3',
        'PyYAML>=5.1',
        'agutil>=4.1.0'
    ],
    classifiers = [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
        "Topic :: Scientific/Engineering :: Interface Engine/Protocol Translator",
    ],
    license="BSD3"
)
