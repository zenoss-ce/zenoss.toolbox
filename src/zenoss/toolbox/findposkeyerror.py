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
scriptSummary = " - scans a ZODB path for POSKeyErrors - "
documentationURL = "https://support.zenoss.com/hc/en-us/articles/203117795"


import abc
import argparse
import datetime
import Globals
import logging
import os
import re
import sys
import time
import traceback
import transaction
import ZenToolboxUtils

from Products.ZenModel.Device import Device
from Products.ZenModel.ZenStatus import ZenStatus
from Products.ZenRelations.RelationshipBase import RelationshipBase
from Products.ZenRelations.ToManyContRelationship import ToManyContRelationship
from Products.ZenUtils.Utils import unused
from Products.ZenUtils.ZenScriptBase import ZenScriptBase
from time import localtime, strftime
try:
    from ZenPacks.zenoss.AdvancedSearch.SearchManager import SearchManager, SEARCH_MANAGER_ID
except ImportError:
    pass
from ZenToolboxUtils import inline_print
from ZODB.POSException import POSKeyError
from ZODB.utils import u64


unused(Globals) 


def progress_bar(items, errors, repairs, fix_value, cycle):
    if fix_value:
        inline_print("[%s]  Cycle %s  | Items Scanned: %12d | Errors:  %6d | Repairs: %6d |  " %
                     (time.strftime("%Y-%m-%d %H:%M:%S"), cycle, items, errors, repairs))
    else:
        inline_print("[%s]  Cycle %s  | Items Scanned: %12d | Errors:  %6d |  " % 
                     (time.strftime("%Y-%m-%d %H:%M:%S"), cycle, items, errors))


class Fixer(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def fixable(self, ex, objId, parentPath, dmd, log):
        """
        Return a no-argument callable object that will perform the fix
        when invoked or None if not fixable.
        """


class RelFixer(Fixer):
    def fixable(self, ex, relId, parentPath, dmd, log):
        """
        Return True if this object can fix the exception.
        """
        try:
            parent = dmd.getObjByPath(parentPath)
            relationship = parent._getOb(relId)
            if not isinstance(relationship, RelationshipBase):
                return None
            badobj = getattr(relationship, "_objects", None)
            if badobj is None:
                log.error("Cannot fix relationship - no _objects attribute")
                return None
            exOID = getOID(ex)
            relOID = getPOID(relationship._objects)
            if exOID == relOID:
                return lambda: self._fix(exOID, relOID, relationship, parent, dmd, log)
            else:
                log.error("Cannot fix this relationship - exOID %s != relOID %s", exOID, relOID)
        except:
            return None

    def _fix(self, exOID, relOID, relationship, parent, dmd, log):
        """ Attempt to fix the POSKeyError """
        log.info("Repairing '_objects' attribute on %s", parent)
        cls = relationship._objects.__class__
        relationship._objects = cls()
        parent._p_changed = True
        transaction.commit()


class SearchManagerFixer(Fixer):
    """
    SearchManagerFixer fixes SearchManager POSKeyErrors like:
        POSKeyError: 0x0683923b on attribute 'SearchManager' of app.zport.dmd.ZenUsers.svs
    """
    # >>> dmd.ZenUsers.svs.SearchManager.__class__
    # <class 'ZenPacks.zenoss.AdvancedSearch.SearchManager.SearchManager'>
    # >>> find('svs')
    # <UserSettings at /zport/dmd/ZenUsers/svs>
    # >>> d=_
    # >>> d._delOb('SearchManager')
    # >>> commit()
    def fixable(self, ex, objId, parentPath, dmd, log):
        """ Return True if this object can fix the exception.  """
        if objId != 'SearchManager':
            return None

        parent = dmd.getObjByPath(parentPath)
        obj = parent._getOb(objId)
        if not isinstance(obj, SearchManager):
            return None
        exOID = getOID(ex)
        relOID = getPOID(obj)
        if exOID == relOID:
            return lambda: self._fix(exOID, parent, dmd, log)

        return None

    def _fix(self, exOID, parent, dmd, log):
        """ Delete only; a new one will be created when a SearchProvider is requested.  """
        try:
            log.info("Repairing 'SearchManager' attribute on %s", parent)
            parent._delOb('SearchManager')
        except Exception as e:
            log.exception(e)
        transaction.commit()

        try:
            parent._setObject(SEARCH_MANAGER_ID, SearchManager(SEARCH_MANAGER_ID))
        except Exception as e:
            log.exception(e)
        transaction.commit()


class ComponentSearchFixer(Fixer):
    """
    ComponentSearchFixer fixes ComponentSearch POSKeyErrors like:
        POSKeyError: 0x070039e0 on attribute 'componentSearch' of app.zport.dmd.Devices.Network.Juniper.mx.mx_240.devices.edge1.fra
    """

    def fixable(self, ex, objId, parentPath, dmd, log):
        """ Return True if this object can fix the exception.  """
        if objId != 'componentSearch':
            return None

        parent = dmd.getObjByPath(parentPath)
        obj = parent._getOb(objId)
        exOID = getOID(ex)
        relOID = getPOID(obj)
        if exOID == relOID:
            return lambda: self._fix(exOID, parent, dmd, log)

        return None

    def _fix(self, exOID, parent, dmd, log):
        """ Attempt to remove and recreate the componentSearch() """
        try:
            log.info("Repairing 'componentSearch' attribute on %s", parent)
            parent._delOb('componentSearch')
        except Exception as e:
            log.exception(e)
        transaction.commit()

        try:
            parent._create_componentSearch()
        except Exception as e:
            log.exception(e)
        transaction.commit()


class OperatingSystemFixer(Fixer):
    """
    OperatingSystemFixer fixes 'os' POSKeyErrors like:
        POSKeyError: 0x9782ff on attribute 'os' of /zport/dmd/Devices/Web/SSL/devices/YOUR_DEVICE_HERE
    """

    def fixable(self, ex, objId, parentPath, dmd, log):
        """ Return True if this object can fix the exception.  """

        if objId != 'os':
            return None

        parent = dmd.getObjByPath(parentPath)
        obj = parent._getOb(objId)
        exOID = getOID(ex)
        relOID = getPOID(obj)
        if exOID == relOID:
            return lambda: self._fix(exOID, parent, dmd, log)

        return None

    def _fix(self, exOID, parent, dmd, log):
        """ Attempt to remove and recreate the os object """
        from Products.ZenModel.OperatingSystem import OperatingSystem
        try:
            log.info("Repairing 'os' attribute on %s - please remodel after script completes", parent)
            parent._delOb('os')
        except Exception as e:
            log.exception(e)
        transaction.commit()

        try:
            temp_os_comp = OperatingSystem()
            parent._setObject(temp_os_comp.id, temp_os_comp)
        except Exception as e:
            log.exception(e)
        transaction.commit()


class HardwareFixer(Fixer):
    """
    HardwareFixer fixes 'hw' POSKeyErrors like:
        POSKeyError: 0x9782fb on attribute 'hw' of /zport/dmd/Devices/Web/SSL/devices/YOUR_DEVICE_HERE
    """

    def fixable(self, ex, objId, parentPath, dmd, log):
        """ Return True if this object can fix the exception.  """

        if objId != 'hw':
            return None

        parent = dmd.getObjByPath(parentPath)
        obj = parent._getOb(objId)
        exOID = getOID(ex)
        relOID = getPOID(obj)
        if exOID == relOID:
            return lambda: self._fix(exOID, parent, dmd, log)

        return None

    def _fix(self, exOID, parent, dmd, log):
        """ Attempt to remove and recreate the hw object """
        from Products.ZenModel.DeviceHW import DeviceHW
        try:
            log.info("Repairing 'hw' attribute on %s - please remodel after script completes", parent)
            parent._delOb('hw')
        except Exception as e:
            log.exception(e)
        transaction.commit()

        try:
            temp_hw_comp = DeviceHW()
            parent._setObject(temp_hw_comp.id, temp_hw_comp)
        except Exception as e:
            log.exception(e)
        transaction.commit()

_fixits = [RelFixer(), SearchManagerFixer(), ComponentSearchFixer(), OperatingSystemFixer(), HardwareFixer(), ]


def _getEdges(node, path_string, attempt_fix, counters, log):
    cls = node.aq_base
    attempted_fix = False

    # Fixes ZEN-18368: findposkeyerror should detect/fix _lastPollSnmpUpTime
    if isinstance(node, Device):
        try:
            try:
                counters['item_count'].increment()
                test_reference = node._lastPollSnmpUpTime
                test_results = test_reference.getStatus()
            #Fixes ZEN-20252: findposkeyerror won't attempt fix on attributeError for getStatus()
            except (POSKeyError, AttributeError) as fixableException:
                if attempt_fix:
                    counters['repair_count'].increment()
                    attempted_fix = True
                    node._lastPollSnmpUpTime = ZenStatus(0)
                    transaction.commit()
                raise
        except Exception as e:
            counters['error_count'].increment()
            log.critical("%s: %s on %s '%s' of %s", type(e).__name__, e, "attribute", "_lastPollSnmpUpTime", path_string)
        if attempted_fix:
            log.info("Repairing '_lastPollSnmpUpTime' attribute on %s", node)

    names = set(node.objectIds() if hasattr(cls, "objectIds") else [])
    relationships = set(
        node.getRelationshipNames()
        if hasattr(cls, "getRelationshipNames") else []
    )
    return (names - relationships), relationships


_RELEVANT_EXCEPTIONS = (POSKeyError, KeyError, AttributeError)


def _getPathStr(path):
    return "app%s" % ('.'.join(path)) if len(path) > 1 else "app"


def fixPOSKeyError(exname, ex, objType, objId, parentPath, dmd, log, counters):
    """
    Fixes POSKeyErrors given:
        Name of exception type object,
        Exception,
        Type of problem object,
        Name (ID) of the object,
        The path to the parent of the named object
    """
    # -- verify that the OIDs match
    for fixer in _fixits:
        fix = fixer.fixable(ex, objId, parentPath, dmd, log)
        if fix:
            counters['repair_count'].increment()
            fix()
            break


def getPOID(obj):
    # from ZODB.utils import u64
    return "0x%08x" % u64(obj._p_oid)


def getOID(ex):
    return "0x%08x" % int(str(ex), 16)


def findPOSKeyErrors(topnode, attempt_fix, use_unlimited_memory, dmd, log, counters, max_cycles):
    """ Processes issues as they are found, handles progress output, logs to output file """

    PROGRESS_INTERVAL = 829  # Prime number near 1000 ending in a 9, used for progress bar

    current_cycle = 0
    if not attempt_fix:
        max_cycles = 1
    number_of_issues = -1
    number_of_repairs = -1

    while ((current_cycle < max_cycles) and (number_of_issues != 0) and (number_of_repairs != 0)):
        # Objects that will have their children traversed are stored in 'nodes'
        print
        current_cycle += 1
        log.info("## Beginning cycle %s of %s (potential)", current_cycle, max_cycles)
        nodes = [topnode]
        counters['item_count'].reset()
        counters['error_count'].reset()
        counters['repair_count'].reset()
        while nodes:
            node = nodes.pop(0)
            counters['item_count'].increment()
            path = node.getPhysicalPath()
            path_string = "/".join(path)

            if (counters['item_count'].value() % PROGRESS_INTERVAL) == 0:
                if not use_unlimited_memory:
                    transaction.abort()
                progress_bar(counters['item_count'].value(), counters['error_count'].value(),
                             counters['repair_count'].value(), attempt_fix, current_cycle)

            try:
                attributes, relationships = _getEdges(node, path_string, attempt_fix, counters, log)
            except _RELEVANT_EXCEPTIONS as e:
                log.critical("%s: %s %s '%s'", type(e).__name__, e, "while retreiving children of", path_string)
                counters['error_count'].increment()
                if attempt_fix:
                    if isinstance(e, POSKeyError):
                        fixPOSKeyError(type(e).__name__, e, "node", name, path, dmd, log, counters)
                continue
            except Exception as e:
                log.exception(e)

            for name in relationships:
                try:
                    if (counters['item_count'].value() % PROGRESS_INTERVAL) == 0:
                        if not use_unlimited_memory:
                            transaction.abort()
                        progress_bar(counters['item_count'].value(), counters['error_count'].value(),
                                     counters['repair_count'].value(), attempt_fix, current_cycle)
                    counters['item_count'].increment()

                    rel = node._getOb(name)
                    rel()
                    # ToManyContRelationship objects should have all referenced objects traversed
                    if isinstance(rel, ToManyContRelationship):
                        nodes.append(rel)
                except SystemError as e:
                    # to troubleshoot traceback in:
                    #   https://dev.zenoss.com/tracint/pastebin/4769
                    # ./findposkeyerror --fixrels /zport/dmd/
                    #   SystemError: new style getargs format but argument is not a tuple
                    log.critical("%s: %s on %s '%s' of %s", type(e).__name__, e, "relationship", name, path_string)
                    raise  # Not sure why we are raising this vs. logging and continuing
                except _RELEVANT_EXCEPTIONS as e:
                    counters['error_count'].increment()
                    log.critical("%s: %s on %s '%s' of %s", type(e).__name__, e, "relationship", name, path_string)
                    if attempt_fix:
                        if isinstance(e, POSKeyError):
                            fixPOSKeyError(type(e).__name__, e, "attribute", name, path, dmd, log, counters)
                except Exception as e:
                    log.critical("%s: %s on %s '%s' of %s", type(e).__name__, e, "relationship", name, path_string)

            for name in attributes:
                try:
                    if (counters['item_count'].value() % PROGRESS_INTERVAL) == 0:
                        if not use_unlimited_memory:
                            transaction.abort()
                        progress_bar(counters['item_count'].value(), counters['error_count'].value(),
                                     counters['repair_count'].value(), attempt_fix, current_cycle)
                    counters['item_count'].increment()
                    if name == "temp_folder" and path_string == "": # skip session db
                        continue
                    childnode = node._getOb(name)
                    childnode.getId()
                except _RELEVANT_EXCEPTIONS as e:
                    counters['error_count'].increment()
                    log.critical("%s: %s on %s '%s' of %s", type(e).__name__, e, "attribute", name, path_string)
                    if attempt_fix:
                        if isinstance(e, POSKeyError):
                            fixPOSKeyError(type(e).__name__, e, "attribute", name, path, dmd, log, counters)
                except Exception as e:
                    log.critical("%s: %s on %s '%s' of %s", type(e).__name__, e, "relationship", name, path_string)
                else:
                    # No exception, so it should be safe to add this child node as a traversable object.
                    nodes.append(childnode)

        if not use_unlimited_memory:
            transaction.abort()

        progress_bar(counters['item_count'].value(), counters['error_count'].value(),
                     counters['repair_count'].value(), attempt_fix, current_cycle)
        number_of_issues = counters['error_count'].value()
        number_of_repairs = counters['repair_count'].value()
        log.info("findposkeyerror cycle %s: examined %d objects, encountered %d errors, and attempted %d repairs",
                  current_cycle, counters['item_count'].value(), counters['error_count'].value(), counters['repair_count'].value())


def main():
    """ Scans through zodb hierarchy (from user-supplied path, defaults to /,  checking for PKEs """

    execution_start = time.time()
    scriptName = os.path.basename(__file__).split('.')[0]
    parser = ZenToolboxUtils.parse_options(scriptVersion, scriptName + scriptSummary + documentationURL)
    # Add in any specific parser arguments for %scriptName
    parser.add_argument("-f", "--fix", action="store_true", default=False,
                        help="attempt to fix ZenRelationship objects")
    parser.add_argument("-n", "--cycles", action="store", default="2", type=int,
                        help="maximum times to cycle (with --fix)")
    parser.add_argument("-p", "--path", action="store", default="/", type=str,
                        help="base path to scan from (Devices.Server)?")
    parser.add_argument("-u", "--unlimitedram", action="store_true", default=False,
                        help="skip transaction.abort() - unbounded RAM, ~40%% faster")
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

    counters = {
        'item_count': ZenToolboxUtils.Counter(0),
        'error_count': ZenToolboxUtils.Counter(0),
        'repair_count': ZenToolboxUtils.Counter(0)
        }

    processed_path = re.split("[./]", cli_options['path'])
    if processed_path[0] == "app":
        processed_path = processed_path[1:]
    processed_path = '/'.join(processed_path) if processed_path else '/'

    try:
        folder = dmd.getObjByPath(processed_path)
    except KeyError:
        print "Invalid path: %s" % (cli_options['path'])
    else:
        print("[%s] Examining items under the '%s' path (%s):" %
              (strftime("%Y-%m-%d %H:%M:%S", localtime()), cli_options['path'], folder))
        log.info("Examining items under the '%s' path (%s)", cli_options['path'], folder)
        findPOSKeyErrors(folder, cli_options['fix'], cli_options['unlimitedram'], dmd, log, counters, cli_options['cycles'])
        print

    print("\n[%s] Execution finished in %s\n" %
          (strftime("%Y-%m-%d %H:%M:%S", localtime()),
           datetime.timedelta(seconds=int(time.time() - execution_start))))
    log.info("findposkeyerror completed in %1.2f seconds", time.time() - execution_start)
    log.info("############################################################")

    if not cli_options['skipEvents']:
        if counters['error_count'].value():
            eventSeverity = 4
            eventSummaryMsg = "%s encountered %d errors (took %1.2f seconds)" % \
                               (scriptName, counters['error_count'].value(), (time.time() - execution_start))
        else:
            eventSeverity = 2
            eventSummaryMsg = "%s completed without errors (took %1.2f seconds)" % \
                               (scriptName, (time.time() - execution_start))

        ZenToolboxUtils.send_summary_event(
            eventSummaryMsg, eventSeverity,
            scriptName, "executionStatus",
            documentationURL, dmd
        )

    if ((counters['error_count'].value() > 0) and not cli_options['fix']):
        print("** WARNING ** Issues were detected - Consult KB article at")
        print("      https://support.zenoss.com/hc/en-us/articles/203117795\n")
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

