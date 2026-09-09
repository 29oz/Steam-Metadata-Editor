# A Metadata Editor for Steam Applications
# Copyright (C) 2023  Tomás Ralph
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import re
import time
from tkinter import filedialog, messagebox


def register_app_in_library(contents, library_path, app_id, value="0"):
    """
    Registers an appID inside the "apps" section of the given library,
    as found in libraryfolders.vdf.

    Returns a tuple with the new contents and whether the operation
    succeeded.
    """
    path_marker = '"path"\t\t"' + library_path + '"'
    path_index = contents.find(path_marker)
    if path_index == -1:
        return contents, False

    library_close = contents.find("\n\t}", path_index)
    if library_close == -1:
        return contents, False

    apps_index = contents.find('"apps"', path_index)
    new_entry = f'\t\t\t"{app_id}"\t\t"{value}"'

    if apps_index == -1 or apps_index > library_close:
        # This library has no "apps" section, create one right
        # before its closing brace
        apps_section = f'\t\t"apps"\n\t\t{{\n{new_entry}\n\t\t}}\n'
        return (
            contents[:library_close + 1] + apps_section + contents[library_close + 1:],
            True,
        )

    brace_index = contents.find("{", apps_index)
    apps_close = contents.find("\n\t\t}", brace_index)
    if brace_index == -1 or apps_close == -1:
        return contents, False

    return contents[:apps_close] + "\n" + new_entry + contents[apps_close:], True


def unregister_app(contents, app_id):
    """
    Removes every mention of the given appID from the "apps"
    sections of libraryfolders.vdf contents.
    """
    pattern = re.compile(r'\n\s*"' + str(app_id) + r'"\s+"[0-9]+"')
    return pattern.sub("", contents)


def build_appmanifest(app_id, name, installdir, buildid="0", last_owner=None):
    """
    Builds the contents of an appmanifest_<appID>.acf file marking
    the application as fully installed (StateFlags 4).
    """

    def sanitize(value):
        return str(value).replace("\\", "").replace('"', "")

    lines = [
        '"AppState"',
        "{",
        f'\t"appid"\t\t"{app_id}"',
        '\t"universe"\t\t"1"',
        f'\t"name"\t\t"{sanitize(name)}"',
        '\t"StateFlags"\t\t"4"',
        f'\t"installdir"\t\t"{sanitize(installdir)}"',
        f'\t"LastUpdated"\t\t"{int(time.time())}"',
        '\t"SizeOnDisk"\t\t"0"',
        f'\t"buildid"\t\t"{buildid}"',
    ]

    if last_owner:
        lines.append(f'\t"LastOwner"\t\t"{last_owner}"')

    lines += [
        '\t"BytesToDownload"\t\t"0"',
        '\t"BytesDownloaded"\t\t"0"',
        '\t"AutoUpdateBehavior"\t\t"0"',
        '\t"AllowOtherDownloadsWhileRunning"\t\t"0"',
        '\t"ScheduledAutoUpdate"\t\t"0"',
        '\t"InstalledDepots"',
        "\t{",
        "\t}",
        '\t"UserConfig"',
        "\t{",
        "\t}",
        "}",
    ]

    return "\n".join(lines) + "\n"


def get_last_owner(steam_path):
    """
    Tries to find the user's SteamID, first in any existing
    appmanifest, then in loginusers.vdf. Returns None if it
    can't be found.
    """
    steamapps_path = os.path.join(steam_path, "steamapps")
    try:
        for file_name in os.listdir(steamapps_path):
            if not file_name.startswith("appmanifest_"):
                continue
            with open(os.path.join(steamapps_path, file_name)) as manifest:
                owner = re.search(
                    r'"LastOwner"\t\t"([0-9]+)"', manifest.read()
                )
            if owner:
                return owner.group(1)
    except OSError:
        pass

    try:
        loginusers_path = os.path.join(steam_path, "config", "loginusers.vdf")
        with open(loginusers_path) as loginusers:
            users = re.findall(r'"([0-9]{17})"', loginusers.read())
        if users:
            return users[-1]
    except OSError:
        pass

    return None


def ask_steam_path():
    messagebox.showinfo(
        title="Can't locate Steam",
        message="Steam couldn't be located in your system, "
        + "or there's no \"appinfo.vdf\" file present. "
        + "Please point to it's installation directory."
    )
    return filedialog.askdirectory()
