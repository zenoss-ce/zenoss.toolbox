##############################################################################
#
# Copyright (C) Zenoss, Inc. 2015, all rights reserved.
#
# This content is made available according to terms specified in
# License.zenoss under the directory where your Zenoss product is installed.
#
##############################################################################

#!/opt/zenoss/bin/python

scriptVersion = "1.1.0"

import argparse
import datetime
import Globals
import logging
import os
import sys
import time
import traceback
import transaction
import ZenToolboxUtils

from Products.ZenUtils.ZenScriptBase import ZenScriptBase
from Products.Zuul.catalog.events import IndexingEvent
from ZODB.transact import transact
from zope.event import notify


def inline_print(message):
    '''Print message on a single line using sys.stdout.write, .flush'''
    sys.stdout.write("\r%s" % (message))
    sys.stdout.flush()

@transact
def index_device(dev, dmd, log):
    try:
        notify(IndexingEvent(dev))
        dev.index_object(noips=True)
    except Exception as e:
        log.exception(e)
    for comp in dev.getDeviceComponentsNoIndexGen():
        try:
            notify(IndexingEvent(comp))
            comp.index_object()
        except Exception as e:
            log.exception(e)

def reindex_dmd_objects(name, type, dmd, log):
    """Performs the reindex.  Returns False if no issues encountered, otherwise True"""
    try:
        inline_print("[%s] Reindexing %s ... " % (time.strftime("%Y-%m-%d %H:%M:%S"), name))
        if not (name == 'Devices'):
            object_reference = eval(type)
            object_reference.reIndex()
            print("finished")
            log.info("%s reIndex() completed successfully", name)
        else: # Special case for Devices, using method from altReindex ZEN-10793
            log.info("Reindexing Devices")
            output_count = 0
            for dev in dmd.Devices.getSubDevicesGen_recursive():
                index_device(dev, dmd, log)
                output_count += 1
                dev._p_deactivate()
                transaction.commit()
                
                if (output_count % 10) == 0:
                    # sync after 10 devices
                    dmd._p_jar.sync()
                    
                    if (output_count % 100) == 0:
                        log.debug("Device Reindex has passed %d devices" % (output_count))
                    inline_print("[%s] Reindexing %s ... %8d devices processed" %
                                 (time.strftime("%Y-%m-%d %H:%M:%S"), "Devices", output_count))
                
            inline_print("[%s] Reindexing %s ... finished                                    " %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), "Devices"))
            print ""
            log.info("%d Devices reindexed successfully" % (output_count))
        dmd._p_jar.sync()
        transaction.commit()
        return False
    except Exception as e:
        print " FAILED  (check log file for details)"
        log.error("%s.reIndex() failed" % (name))
        log.exception(e)
        return True


def parse_options():
    """Defines command-line options for script """

    parser = argparse.ArgumentParser(version=scriptVersion,
                                     description="Reindexes top-level organizers. Documentation available at "
                                     "https://support.zenoss.com/hc/en-us/articles/203263689")

    parser.add_argument("-v10", "--debug", action="store_true", default=False,
                        help="verbose log output (debug logging)")
    parser.add_argument("-l", "--list", action="store_true", default=False,
                        help="output all supported reIndex() types")
    parser.add_argument("-t", "--type", action="store", default="",
                        help="specify which type to reIndex()")

    return vars(parser.parse_args())


def main():
    '''Performs reindex call on different DMD categories (used to be a part of zencatalogscan)'''

    execution_start = time.time()
    scriptName = os.path.basename(__file__).split('.')[0]
    cli_options = parse_options()
    log, logFileName = ZenToolboxUtils.configure_logging(scriptName, scriptVersion)
    log.info("Command line options: %s" % (cli_options))
    if cli_options['debug']:
        log.setLevel(logging.DEBUG)

    # Attempt to get the zenoss.toolbox lock before any actions performed
    if not ZenToolboxUtils.get_lock("zenoss.toolbox", log):
        sys.exit(1)

    print "\n[%s] Initializing %s v%s (detailed log at %s)" % \
          (time.strftime("%Y-%m-%d %H:%M:%S"), scriptName, scriptVersion, logFileName)

    # Obtain dmd ZenScriptBase connection
    dmd = ZenScriptBase(noopts=True, connect=True).dmd
    log.debug("ZenScriptBase connection obtained")

    any_issue = False

    # Else build list of catalogs, then process catalog(s) and perform reindex if --fix
    types_to_reIndex = {
        'Devices': 'dmd.Devices',
        'Events': 'dmd.Events',
        'Manufacturers': 'dmd.Manufacturers',
        'Networks': 'dmd.Networks',
        'Services': 'dmd.Services'
        }

    if cli_options['list'] or not cli_options['type'] :
        # Output list of present catalogs to the UI, perform no further operations
        print "List of dmd types that support reIndex() calls from this script:\n"
        print "\n".join(types_to_reIndex.keys())
        log.info("Zenreindextool finished - list of supported types output to CLI")
        exit(1)

    if cli_options['type'] in types_to_reIndex.keys():
        any_issue = reindex_dmd_objects(cli_options['type'], types_to_reIndex[cli_options['type']], dmd, log)
    else:
        print("Type '%s' unrecognized - unable to reIndex()" % (cli_options['type']))
        log.error("CLI input '%s' doesn't match recognized types" % (cli_options['type']))
        exit(1)

    # Print final status summary, update log file with termination block
    print("\n[%s] Execution finished in %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                 datetime.timedelta(seconds=int(time.time() - execution_start))))
    log.info("zenindextool completed in %1.2f seconds" % (time.time() - execution_start))
    log.info("############################################################")

    if any_issue:
        print("** WARNING ** Issues were encountered - Consult KB article at")
        print("      https://support.zenoss.com/hc/en-us/articles/203263689\n")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
