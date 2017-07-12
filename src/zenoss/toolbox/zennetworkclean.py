##############################################################################
#
# Copyright (C) Zenoss, Inc. 2016, all rights reserved.
#
# This content is made available according to terms specified in
# License.zenoss under the directory where your Zenoss product is installed.
#
##############################################################################
#!/opt/zenoss/bin/python

scriptVersion = "2.0.0"
scriptSummary = " - removes old and/or unused ip addresses - "
documentationURL = "https://support.zenoss.com/hc/en-us/articles/203263699"


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

from Acquisition import aq_parent
from Products.ZenUtils.ZenScriptBase import ZenScriptBase
from ZenToolboxUtils import inline_print
from ZODB.transact import transact


def scan_progress_message(done, fix, cycle, catalog, issues, total_number_of_issues, percentage, chunk, log):
    '''Handle output to screen and logfile, remove output from scan_catalog logic'''
    # Logic for log file output messages based on done, issues
    if not done:
        log.debug("Scan of %s catalog is %2d%% complete" % (catalog, 2*chunk))
    else:
        if issues > 0:
            log.warning("Scanned %s - found %d stale reference(s)" % (catalog, issues))
        else:
            log.info("No stale references found scanning: %s" % (catalog))
        log.debug("Scan of %s catalog is complete" % (catalog))
    # Logic for screen output messages based on done, issues, and fix
    if issues > 0:
        if fix:
            if not done:
                inline_print("[%s]  Cleaning  [%-50s] %3d%% [%d orphaned IPs are deleted]" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), '='*chunk, 2*chunk, issues))
            else:
                inline_print("[%s]  Clean #%2.0d [%-50s] %3.0d%% [%d orphaned IPs are deleted]\n" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), cycle, '='*50, 100, issues))
        else:
            if not done:
                inline_print("[%s]  Scanning  [%-50s] %3d%% [%d orphaned IPs are detected]" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), '='*chunk, 2*chunk, issues))
            else:
                inline_print("[%s]  WARNING   [%-50s] %3.0d%% [There are %d orphaned IPs (%.1f%%)]\n" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), '='*50, 100, issues, percentage))
    else:
        if not done:
            inline_print("[%s]  Scanning  [%-50s] %3d%% " %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), '='*chunk, 2*chunk))
        else:
            if (total_number_of_issues == 0):
                inline_print("[%s]  Verified  [%-50s] %3.0d%% [No issues] \n" %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), '='*50, 100))
            else:
                inline_print("[%s]  Verified  [%-50s] %3.0d%% [%d orphaned IPs are deleted (%.1f%%)] \n" %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), '='*50, 100, total_number_of_issues, percentage))

 
@transact
def scan_catalog(catalog_name, catalog_list, fix, max_cycles, dmd, log):
    """Scan through a catalog looking for broken references"""

    catalog = catalog_list[0]
    initial_catalog_size = catalog_list[1]

    print("[%s] Examining %-35s (%d Objects)" %
          (time.strftime("%Y-%m-%d %H:%M:%S"), catalog_name, initial_catalog_size))
    log.info("Examining %s catalog with %d objects" % (catalog_name, initial_catalog_size))

    number_of_issues = -1
    total_number_of_issues = 0
    current_cycle = 0
    if not fix:
        max_cycles = 1

    while ((current_cycle < max_cycles) and (number_of_issues != 0)):
        number_of_issues = 0
        current_cycle += 1
        if (fix):
            log.info("Beginning cycle %d for catalog %s" % (current_cycle, catalog_name))
        scanned_count = 0
        progress_bar_chunk_size = 1

        # ZEN-12165: show progress bar immediately before 'for' time overhead, before loading catalog
        scan_progress_message(False, fix, current_cycle, catalog_name, 0, 0, 0, 0, log)

        try:
            brains = catalog()
            catalog_size = len(brains)
            if (catalog_size > 50):
                progress_bar_chunk_size = (catalog_size//50) + 1
        except Exception:
            raise

        for brain in brains:
            scanned_count += 1
            if (scanned_count % progress_bar_chunk_size) == 0:
                chunk_number = scanned_count // progress_bar_chunk_size
                scan_progress_message(False, fix, current_cycle, catalog_name, number_of_issues, 0, 0, chunk_number, log)           
            try:
                ip = brain.getObject()
                if not ip.interface():
                    if not fix:
                        ip._p_deactivate()
                    raise Exception
                ip._p_deactivate()
            except Exception:
                number_of_issues += 1
                log.warning("Catalog %s contains orphaned object %s" % (catalog_name, ip.viewName()))
                if fix:
                    log.info("Attempting to delete %s" % (ip.viewName()))
                    try:
                        parent = aq_parent(ip)
                        parent._delObject(ip.id)
                        ip._p_deactivate()

                    except Exception as e:
                        log.exception(e)
        total_number_of_issues += number_of_issues
        percentage = total_number_of_issues*1.0/initial_catalog_size*100
        scan_progress_message(True, fix, current_cycle, catalog_name, number_of_issues, total_number_of_issues, percentage, chunk_number, log)
        # Commit the transaction so that any removed IPs will get unindexed
        transaction.commit()

    if number_of_issues > 0:
        # print 'total_number_of_issues: {0}'.format(total_number_of_issues)
        return True, number_of_issues
    return False


def build_catalog_dict(dmd, log):
    """Builds a list of catalogs present and > 0 objects"""

    catalogs_to_check = {
        'Networks.ipSearch': 'dmd.Networks.ipSearch',
        'IPv6Networks.ipSearch': 'dmd.IPv6Networks.ipSearch',
        }

    log.debug("Checking %d supported catalogs for (presence, not empty)" % (len(catalogs_to_check)))

    intermediate_catalog_dict = {}

    for catalog in catalogs_to_check.keys():
        try:
            temp_brains = eval(catalogs_to_check[catalog])
            if len(temp_brains) > 0:
                log.debug("Catalog %s exists, has items - adding to list" % (catalog))
                intermediate_catalog_dict[catalog] = [eval(catalogs_to_check[catalog]), len(temp_brains)]
            else:
                log.debug("Skipping catalog %s - exists but has no items" % (catalog))
        except AttributeError:
            log.debug("Skipping catalog %s - catalog not found" % (catalog))
        except Exception, e:
            log.exception(e)

    return intermediate_catalog_dict


def main():
    '''Checks for old/unused ip addresses.  If --fix, attempts to remove old unused ip addresses.
       Builds list of available non-empty catalogs.'''

    execution_start = time.time()
    scriptName = os.path.basename(__file__).split('.')[0]
    parser = ZenToolboxUtils.parse_options(scriptVersion, scriptName + scriptSummary + documentationURL)
    # Add in any specific parser arguments for %scriptName
    parser.add_argument("-f", "--fix", action="store_true", default=False,
                        help="attempt to remove any stale references")
    parser.add_argument("-n", "--cycles", action="store", default="12", type=int,
                        help="maximum times to cycle (with --fix)")
    parser.add_argument("-l", "--list", action="store_true", default=False,
                        help="output all supported catalogs")
    parser.add_argument("-c", "--catalog", action="store", default="",
                        help="only scan/fix specified catalog")
    cli_options = vars(parser.parse_args())
    log, logFileName = ZenToolboxUtils.configure_logging(scriptName, scriptVersion, cli_options['tmpdir'])
    log.info("Command line options: %s" % (cli_options))
    if cli_options['debug']:
        log.setLevel(logging.DEBUG)

    print "\n[%s] Initializing %s v%s (detailed log at %s)" % \
          (time.strftime("%Y-%m-%d %H:%M:%S"), scriptName, scriptVersion, logFileName)

    # Attempt to get the zenoss.toolbox lock before any actions performed
    if not ZenToolboxUtils.get_lock("zenoss.toolbox", log):
        sys.exit(1)

    # Obtain dmd ZenScriptBase connection
    dmd = ZenScriptBase(noopts=True, connect=True).dmd
    log.debug("ZenScriptBase connection obtained")

    any_issue = [False, 0]
    unrecognized_catalog = False

    # Build list of catalogs, then process catalog(s) and perform reindex if --fix
    present_catalog_dict = build_catalog_dict(dmd, log)
    if cli_options['list']:
    # Output list of present catalogs to the UI, perform no further operations
        print "List of supported Zenoss catalogs to examine:\n"
        print "\n".join(present_catalog_dict.keys())
        log.info("zennetworkclean finished - list of supported catalogs output to CLI")
    else:
    # Scan through catalog(s) depending on --catalog parameter
        if cli_options['catalog']:
            if cli_options['catalog'] in present_catalog_dict.keys():
            # Catalog provided as parameter is present - scan just that catalog
                any_issue = scan_catalog(cli_options['catalog'], present_catalog_dict[cli_options['catalog']],
                                         cli_options['fix'], cli_options['cycles'], dmd, log)
            else:
                unrecognized_catalog = True
                print("Catalog '%s' unrecognized - unable to scan" % (cli_options['catalog']))
                log.error("CLI input '%s' doesn't match recognized catalogs" % (cli_options['catalog']))
        else:
        # Else scan for all catalogs in present_catalog_dict
            for catalog in present_catalog_dict.keys():
                any_issue = scan_catalog(catalog, present_catalog_dict[catalog], cli_options['fix'],
                                         cli_options['cycles'], dmd, log) or any_issue

    # Print final status summary, update log file with termination block
    print("\n[%s] Execution finished in %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                 datetime.timedelta(seconds=int(time.time() - execution_start))))
    log.info("zennetworkclean completed in %1.2f seconds" % (time.time() - execution_start))
    log.info("############################################################")

    if any_issue and not cli_options['fix']:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

