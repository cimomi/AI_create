"""Path resolution shared by the whole maintenance toolchain.

No username is ever hard-coded. Defaults to $HOME/Skills; override with
environment variables to run the tools on a different machine or layout:

    SKILLS_HOME=/home/me
    SKILLS_ROOT=/home/me/Skills
"""
import os
from pathlib import Path

HOME = Path(os.environ.get('SKILLS_HOME') or Path.home())
ROOT = Path(os.environ.get('SKILLS_ROOT') or (HOME / 'Skills'))
