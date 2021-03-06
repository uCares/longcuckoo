#!/usr/bin/env python
# Copyright (C) 2010-2014 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import argparse
import logging
import os.path
import sys

sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from lib.cuckoo.core.database import Database, TASK_RECURRENT
from lib.cuckoo.common.utils import time_duration


MACHINE_CRONTAB = """
#!/bin/sh
# Copyright (C) 2010-2014 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

# This script should be run under the "cuckoo" user (assuming that's the
# user under which Cuckoo Sandbox runs).
set -e

CUCKOO="%(cuckoo)s"
EXPERIMENT="$CUCKOO/utils/experiment.py"

# We strive to have at least 5 provisioned virtual machines at any point.
while [ "$("$EXPERIMENT" count-available-machines verbose=false)" -lt 5 ]; do
    IPADDR="$("$EXPERIMENT" allocate-ipaddr verbose=false)"
    EGGNAME="$("$EXPERIMENT" allocate-eggname verbose=false)"
    vmcloak-clone -r --bird bird0 --hostonly-ip "$IPADDR" \\
        --cuckoo "$CUCKOO" "$EGGNAME" --tags longterm
done
"""

def allocate_ip_address():
    """Allocate the next available IP address."""
    # TODO Unuglify this code.
    # TODO Add multiple subnet support.
    ips = []
    for machine in db.list_machines():
        ip = machine.ip.split(".")
        if len(ip) != 4 or ip[0] != "192" or ip[1] != "168" or ip[2] != "56":
            continue

        ips.append(int(ip[3]))

    next_ip = max(ips) + 1 if ips else 3
    if next_ip == 254:
        print "Ran out of IP addresses for this subnet.."
        return

    return "192.168.56.%d" % next_ip


def allocate_eggname():
    """Allocate the next available eggname."""
    # TODO Add support for egg numbes with three digits.
    return "egg%02d" % (len(db.list_machines()) + 1)


class ExperimentManager(object):
    ARGUMENTS = {
        "help": "action",
        "list": "",
        "new": "name path | timeout delta tags options",
        "schedule": "name | delta timeout",
        "count_available_machines": "| verbose",
        "machine_cronjob": "",
        "allocate_ipaddr": "| verbose",
        "allocate_eggname": "| verbose",
        "delta": "name | delta",
        "timeout": "name | timeout",
    }

    def check_arguments(self, action, args, kwargs):
        opt = False
        for idx, arg in enumerate(self.ARGUMENTS[action].split()):
            if arg == "|":
                opt = True
                continue

            if not opt and idx >= len(args) and arg not in kwargs:
                return arg

    def handle_help(self, action):
        """Show help on an action.

        action  = Action to get help on.

        """
        action = action.replace("-", "_")
        if not hasattr(self, "handle_%s" % action):
            print "Unknown action:", action
            exit(1)

        first, doc = True, getattr(self, "handle_%s" % action).__doc__
        for line in doc.strip().split("\n"):
            if not line.strip():
                first = False

            if line.strip().startswith("[") and line.strip().endswith("]"):
                print "Opt.  ", line.strip()[1:-1]
            elif not first:
                print "      ", line.strip()
            else:
                print line.strip()

    def handle_list(self):
        """List all available experiments."""
        print "%20s | %16s" % ("Name", "Experiment Count")
        fmt = "%(name)20s | %(count)16d"
        for experiment in db.list_experiments():
            print fmt % dict(name=experiment.name, count=len(experiment.tasks))

    def handle_new(self, name, path, timeout="1d", delta="1d", tags="",
                   options=""):
        """Create a new experiment.

        name    = Experiment name.
        path    = File path.
        [timeout = Duration of the analysis.]
        [delta   = Relative time between the last and the next task.]
        [tags    = Extra tags.]
        [options = Extra options.]

        """
        # TODO Add more options which don't seem too relevant at the moment.
        task_id = db.add_path(file_path=path,
                              timeout=time_duration(timeout),
                              tags="longterm," + tags,
                              options=options,
                              name=name,
                              repeat=TASK_RECURRENT,
                              delta=delta)

        print "Created experiment '%s' with ID: %d" % (name, task_id)

    def handle_schedule(self, name, delta="1d", timeout="1d"):
        """Schedule the next time and date for an experiment.

        name    = Experiment name.
        [delta   = Relative time after the last task.]
        [timeout = Duration of the analysis.]

        """
        experiment = db.view_experiment(name=name)
        last_task = experiment.tasks.order_by("id desc").first()
        if not last_task:
            print "Tasks with experiment name '%s' not found.." % name
            exit(1)

        task = db.schedule(last_task.id, delta=time_duration(delta),
                           timeout=time_duration(timeout))
        print "Scheduled experiment '%s' with ID: %d" % (name, task.id)

    def handle_delta(self, name, delta=None):
        """Get or set the delta between multiple analyses for the upcoming
        analysis of an experiment.

        name    = Experiment name.
        [delta   = Updated relative time after the last task.]

        """
        if delta is not None:
            db.update_experiment(name, delta=delta)

    def handle_timeout(self, name, timeout=None):
        """Get or set the duration of an analysis for the upcoming analysis
        of an experiment.

        name    = Experiment name.
        [timeout = Updated duration of the analysis.]

        """
        if timeout is not None:
            db.update_experiment(name, timeout=timeout)

    def handle_count_available_machines(self, verbose=True):
        """Count the available machines for longterm analysis.

        [verbose = Verbose output.]

        """
        # TODO Allow tags to be specified.
        if verbose:
            print "Available machines:", db.count_machines_available()
        else:
            print db.count_machines_available()

    def handle_machine_cronjob(self, action="dump", path=None):
        """Manage the machine cronjob - for provisioning virtual machines
        for longterm analysis.

        [action  = Action to perform.]
        [path    = Cronjob path in install mode.]

        """
        cuckoo = os.path.abspath(os.path.join(__file__, "..", ".."))
        cronjob = MACHINE_CRONTAB.strip() % dict(cuckoo=cuckoo)

        if action == "dump":
            print cronjob
        elif action == "install":
            open(path, "wb").write(cronjob)

    def handle_allocate_ipaddr(self, verbose=True):
        """Calculate the next available IP address. If a new network interface
        is required to allocate a new IP address, then this is also handled.
        Doesn't actually allocate a new IP address but merely calculates it.

        [verbose = Verbose output.]

        """
        ip = allocate_ip_address()
        if not ip:
            exit(1)

        if verbose:
            print "Next IP address:", ip
        else:
            print ip

    def handle_allocate_eggname(self, verbose=True):
        """Calculate the next available egg name. Doesn't actually allocate a
        new egg name but merely calculates it.

        [verbose = Verbose output.]

        """
        eggname = allocate_eggname()
        if not eggname:
            exit(1)

        if verbose:
            print "Next egg name:", eggname
        else:
            print eggname


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", type=str, help="Action to perform")
    parser.add_argument("arguments", type=str, nargs="*", help="Arguments for the action")
    args = parser.parse_args()

    action = args.action.replace("-", "_")

    em = ExperimentManager()
    if not hasattr(em, "handle_%s" % action):
        print "Invalid action:", action
        exit(1)

    values = {
        "true": True,
        "false": False,
    }

    args_, kwargs = [], {}
    for arg in args.arguments:
        if "=" in arg:
            k, v = arg.split("=", 1)
            kwargs[k.strip()] = values.get(v.strip(), v.strip())
        else:
            args_.append(arg.strip())

    ret = em.check_arguments(action, args_, kwargs)
    if ret:
        print "Missing argument:", ret
        print
        em.handle_help(action)
        exit(1)

    getattr(em, "handle_%s" % action)(*args_, **kwargs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = Database()
    main()
