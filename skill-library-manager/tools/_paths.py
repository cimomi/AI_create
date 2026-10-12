"""路径解析：整个维护工具链共用，不写死任何用户名。

默认取当前用户主目录下的 Skills；需要时用环境变量覆盖，便于在其他机器或
其他目录结构上复用：

    SKILLS_HOME=/home/me
    SKILLS_ROOT=/home/me/Skills
"""
import os
from pathlib import Path

HOME = Path(os.environ.get('SKILLS_HOME') or Path.home())
ROOT = Path(os.environ.get('SKILLS_ROOT') or (HOME / 'Skills'))
