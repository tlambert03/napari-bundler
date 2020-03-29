#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import glob
import logging
import shutil
import stat
import sys
from datetime import datetime
from os import chmod, environ, lstat, makedirs, path, remove, symlink, listdir
from subprocess import run
from time import time
from typing import List
from urllib.request import urlretrieve

MINICONDA_URL = "https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh"
CONDA_BASE = ""


def safe_conda_base(buildpath: str) -> str:
    """Return path to a 'safe' location (no spaces) for the base conda install.

    Parameters
    ----------
    buildpath : str
        The buildpath for the current bundle.  Will prefer putting stuff into the build
        path, unless there are spaces... in which case it will go in ``~/_temp_conda``

    Returns
    -------
    str
        path to a location where conda can be installed
    """
    buildpath = path.abspath(path.expanduser(args.buildpath))
    conda_dir = path.join(buildpath, "conda")
    if " " not in conda_dir:
        return conda_dir

    # TODO: is there a better way to handle spaces in the target dir?
    alt_dir = path.abspath(path.expanduser("~/_temp_conda"))
    logging.warning(
        f"SPACE found in target conda directory: {conda_dir}\n"
        f"\tusing alternative path: {alt_dir}"
    )
    return alt_dir


def install_conda(buildpath: str) -> str:
    global CONDA_BASE
    conda_dir = safe_conda_base(buildpath)
    CONDA_BASE = conda_dir

    if not path.exists(conda_dir):
        logging.info(f"Installing miniconda to {conda_dir}")
        miniconda_installer = path.join(buildpath, "miniconda_installer.sh")
        if not path.exists(miniconda_installer):
            urlretrieve(MINICONDA_URL, filename=miniconda_installer)
        run(["bash", f"{miniconda_installer}", "-b", "-p", f'"{conda_dir}"'])
    else:
        logging.info(f"Using existing miniconda installation at {conda_dir}")
    return conda_dir


def conda_run(args: List[str], env_name: str = "base"):
    """Run a command from the conda base (or ``env_name``).

    This function puts the corresponding conda environment binaries and site-packages
    at the front of the PATH and PYTHONPATH environmental variables before running the
    command.

    Parameters
    ----------
    args : List[str]
        standard command string as would be provided to subprocess.run
    env_name : str, optional
        Optional name of a conda environment in which to run command, by default "base"
    """
    assert path.isdir(CONDA_BASE), f"Could not find conda environment at {CONDA_BASE}"
    env = environ.copy()
    env["PATH"] = f"{path.join(CONDA_BASE, 'bin')}:{environ.get('PATH')}"
    env["PYTHONPATH"] = ":".join(glob.glob(CONDA_BASE + "/lib/python*/site-packages"))
    if env_name != "base":
        env_dir = path.join(CONDA_BASE, "envs", env_name)
        env["PATH"] = f"{path.join(env_dir, 'bin')}:{env['PATH']}"
        env_pkgs = glob.glob(env_dir + "/lib/python*/site-packages")
        env["PYTHONPATH"] = ":".join(env_pkgs)
    logging.debug(f"ENV_RUN: {' '.join(args)}")
    run(args, env=env)


def create_env(
    conda_base: str, app_name: str, pyversion: str = "3.8", pip_install: List[str] = []
) -> str:
    """Create a new conda environment in ``conda_base``/envs. 

    Parameters
    ----------
    conda_base : str
        Directory of conda installation to use
    app_name : str
        [description]
    pyversion : str, optional
        [description], by default "3.8"
    pip_install : List[str], optional
        [description], by default []
    
    Returns
    -------
    str
        [description]
    """
    env_dir = path.join(conda_base, "envs", app_name)
    if path.exists(env_dir):
        logging.info(f"Deleting existing conda environment {app_name}")
        shutil.rmtree(env_dir)
    logging.info(f"Creating conda environment {app_name}")
    conda_run(
        [
            "conda",
            "create",
            "-n",
            app_name,
            "-c",
            "conda-forge",
            "-y",
            f"python={pyversion}",
        ]
    )
    if not pip_install:
        pip_install = [app_name]
    logging.info("Installing packages with pip")
    # ignore-installed is important otherwise deps that are in the base environment
    # may not make it into the bundle
    conda_run(["pip", "install", "--ignore-installed"] + pip_install, app_name)

    # # here is how you would install using conda
    # logging.info("Installing packages with conda")
    # conda_run(["conda", "install", "-n", app_name, "-y", app_name])

    return env_dir


def bundle_conda_env(
    env_dir: str, app_path: str, include: List[str] = [], exclude: List[str] = [],
):
    app_resource_dir = path.join(app_path, "Contents", "Resources")
    if not include:
        include = listdir(env_dir)
    for item in include:
        fullpath = path.join(env_dir, item)
        dest = path.join(app_resource_dir, item)
        logging.info(f"Copying {fullpath} to bundle")
        if path.isdir(fullpath):
            shutil.copytree(
                fullpath, dest, symlinks=True,
            )
        else:
            shutil.copy(fullpath, dest)

    for pattern in exclude:
        full_path = path.join(app_resource_dir, pattern)
        for item in glob.glob(full_path):
            try:
                if path.isdir(item):
                    logging.info(f"Removing folder: {item}")
                    shutil.rmtree(item)
                elif path.isfile(item):
                    logging.info(f"Removing file: {item}")
                    remove(item)
                else:
                    logging.error(f"File not found: {item}")
            except (IOError, OSError):
                logging.error(f"could not delete {item}")


def get_confirmation(question: str, default_yes: bool = True) -> bool:
    question = question + (" ([y]/n): " if default_yes else " (y/[n]): ")
    resp = input(question)
    while resp not in ["y", "n", ""]:
        resp = input(question)
    if (resp == "" and not default_yes) or resp == "n":
        return False
    return True


def create_app_folder(name: str, distpath: str, confirm: bool = True) -> str:
    """ Create an app bundle """
    app_name = f"{name}.app"
    distpath = path.abspath(path.expanduser(args.distpath))
    app_path = path.join(distpath, app_name)
    # Check if app already exists and ask user what to do if so.
    if path.exists(app_path):
        if confirm and not get_confirmation("App already exists, overwrite?"):
            logging.info("Skipping app creation")
            return app_path
        logging.info("Removing previous app")
        shutil.rmtree(app_path)

    for folder in ("MacOS", "Resources", "Frameworks"):
        makedirs(path.join(app_path, "Contents", folder))
    return app_path


def create_exe(app_path: str):
    """ Create runnable script in bundle.app/Contents/MacOS"""
    app_name = path.basename(app_path).strip(".app")
    exe_path = path.join(app_path, "Contents", "MacOS", app_name)

    with open(exe_path, "w") as fp:
        try:
            fp.write(
                "#!/usr/bin/env bash\n"
                'script_dir=$(dirname "$(dirname "$0")")\n'
                'export PATH=:"$script_dir/Resources/bin/":$PATH\n'
                '"$script_dir/Resources/bin/python" '
                '"$script_dir/Resources/bin/{}" $@'.format(app_name)
            )
        except IOError:
            logging.error(f"Could not create Contents/MacOS/{app_name} script")
            sys.exit(1)

    # Set execution flags
    current_permissions = stat.S_IMODE(lstat(exe_path).st_mode)
    chmod(exe_path, current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def create_info_plist(
    app_path: str,
    app_name: str,
    icon_name: str = "",
    version: str = "0.1.0",
    app_author: str = "",
    copyright: str = "",
):
    plist_template = path.join(path.dirname(__file__), "Info.template.plist")
    with open(plist_template, "r") as f:
        template = f.read()
    template = template.replace("{{ app_name }}", app_name)
    template = template.replace("{{ app_author }}", app_author or app_name)
    template = template.replace("{{ app_icon }}", icon_name)
    template = template.replace("{{ app_version }}", version)
    template = template.replace("{{ year }}", str(datetime.now().year))
    template = template.replace(
        "{{ copyright }}", copyright or f"{app_name} contributors"
    )
    with open(path.join(app_path, "Contents", "Info.plist"), "w") as f:
        f.write(template)


def copy_icon(icon: str, app_path: str) -> str:
    if not icon:
        icon = path.join(path.dirname(__file__), "icon.icns")
    icon = path.abspath(path.expanduser(args.icon))
    if path.isfile(icon):
        logging.info(f"Copying icon from {icon} to bundle")
        icon_basename = path.basename(icon)
        shutil.copy(icon, path.join(app_path, "Contents", "Resources", icon_basename))
    else:
        logging.warning(f"Could not find icon at {icon}")
        icon_basename = ""
    return icon_basename


def make_dmg(app_path: str, keep_app: bool = False):
    dmg_dir = path.join(path.dirname(app_path), "dmg")
    dmg_file = app_path.replace(".app", ".dmg")
    makedirs(dmg_dir, exist_ok=True)
    if not path.exists(path.join(dmg_dir, "Applications")):
        symlink("/Applications", path.join(dmg_dir, "Applications"))
    if keep_app:
        shutil.copytree(app_path, dmg_dir)
    else:
        shutil.move(app_path, dmg_dir)
    logging.info("Creating DMG archive...")
    result = run(
        ["hdiutil", "create", f"{dmg_file}", "-srcfolder", f"{dmg_dir}"],
        capture_output=True,
    )
    if result.returncode == 0:
        logging.info("DMG successfully created")
        shutil.rmtree(dmg_dir)
    else:
        logging.error(f"DMG creation failed: {result.stderr.decode().strip()}")


def main(args: argparse.Namespace) -> str:
    logging.info(f'Creating "{args.name}.app"')
    start_t = time()

    # create dist/appname.app/ and all subdirectories
    app_path = create_app_folder(args.name, args.distpath, not args.noconfirm)
    # create dist/appname.app/Contents/MacOS/appname script
    create_exe(app_path)
    # download and install miniconda into buildpath
    makedirs(args.buildpath, exist_ok=True)
    conda_base = install_conda(args.buildpath)
    # create a new environment and install app named args.name
    env_dir = create_env(conda_base, args.name, args.py, args.pip_install)
    # move newly-created environment into dist/appname.app/Contents/Resources
    bundle_conda_env(env_dir, app_path, args.conda_include, args.conda_exclude)
    # put icon into dist/appname.app/Contents/Resources
    icon_basename = copy_icon(args.icon, app_path)
    # create Info.plist in dist/appname.app/Contents
    create_info_plist(app_path, args.name, icon_basename)

    if not args.nodmg:
        make_dmg(app_path)

    logging.info(f"App created in {int(time() - start_t)} seconds")
    return app_path


if __name__ == "__main__":

    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument(
        "-y",
        "--noconfirm",
        help="Replace output directory without asking for confirmation",
        action="store_true",
    )
    parser.add_argument(
        "-n",
        "--name",
        help="Name of pip-installable app to bundle. (defaul `napari`)",
        type=str,
        metavar="",
        default="napari",
    )
    parser.add_argument(
        "-i",
        "--icon",
        help=(
            "Icon file (in icns format) for the bundle."
            "\nBy default, looks for 'icon.icns' in same directory"
        ),
        metavar="",
        default="",
        type=str,
    )
    parser.add_argument(
        "--distpath",
        help="Where to put the bundled app (default: ./dist)",
        type=str,
        metavar="",
        default="./dist",
    )
    parser.add_argument(
        "--buildpath",
        help="Where to put build resources (default: ./build)",
        type=str,
        metavar="",
        default="./build",
    )
    parser.add_argument(
        "--log-level",
        help=(
            "Amount of detail in build-time console messages."
            "\nmay be one of TRACE, DEBUG, INFO, WARN,"
            "ERROR, CRITICAL\n(default: INFO)"
        ),
        type=str,
        metavar="",
        default="INFO",
        choices=["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"],
    )
    parser.add_argument(
        "--py",
        help="Python version to bundle. (default 3.8)",
        type=str,
        metavar="",
        default="3.8",
        choices=["3.6", "3.7", "3.8"],
    )
    parser.add_argument(
        "--conda-include",
        help="directories in conda environment to include when bundling",
        type=str,
        metavar="",
        nargs="*",
        default=[],
    )
    parser.add_argument(
        "--conda-exclude",
        help="glob patterns (from base conda environment) to exclude when bundling",
        type=str,
        metavar="",
        nargs="*",
        default=["bin/*-qt4*"],
    )
    parser.add_argument(
        "--pip-install",
        help=(
            "Install these pip packages. Multiple arguments accepted\n"
            "as would be passed to pip install. By default, will attempt\n"
            "to `pip install name` using --name argument"
        ),
        nargs="*",
        metavar="",
        default=[],
    )
    parser.add_argument(
        "--nodmg",
        help="Do not package app into .dmg file.  Default is true.",
        action="store_true",
    )
    parser.add_argument(
        "--clean",
        help="Delete all folders created by this bundler.",
        action="store_true",
    )

    args = parser.parse_args()
    logging.basicConfig(level=args.log_level)

    if args.clean:
        logging.info("Deleting (local) conda installation")
        shutil.rmtree(safe_conda_base(args.buildpath), ignore_errors=True)
        logging.info("Deleting distpath folder")
        shutil.rmtree(args.distpath, ignore_errors=True)
        logging.info("Deleting buildpath folder")
        shutil.rmtree(args.buildpath, ignore_errors=True)
    else:
        main(args)
