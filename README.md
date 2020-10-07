## Installation

All the dependencies are declared in `setup.py` so this can be installed
with [pip](https://pip.pypa.io/). Python 3.5+ is required.

When working from trunk it can be helpful to use `virtualenv`:

    $ virtualenv --python=python3.6 maas && source maas/bin/activate
    $ pip install --user git+https://github.com/maas/python-libmaas.git
    $ maas --help

Releases are periodically made to [PyPI](https://pypi.python.org/) but,
at least for now, it makes more sense to work directly from trunk.

## Configuration

See sample config.yaml

## Alternatives

1. Terraform provider (https://github.com/negronjl/terraform-provider-maas) - Doesn't support storage nor networking configuration.
2. MAAS preseeds (https://docs.maas.io/2.5/en/nodes-custom). Doesn't support tagging, network setup via curtin write_files. Every machine needs dedicated named template.

## Documentation

1. http://maas.github.io/python-libmaas
2. https://github.com/maas/python-libmaas
