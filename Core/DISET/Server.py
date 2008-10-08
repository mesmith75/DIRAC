# $Header: /tmp/libdirac/tmp.stZoy15380/dirac/DIRAC3/DIRAC/Core/DISET/Server.py,v 1.33 2008/10/08 10:35:55 acasajus Exp $
__RCSID__ = "$Id: Server.py,v 1.33 2008/10/08 10:35:55 acasajus Exp $"

import socket
import sys
import os
import time
import select
from DIRAC.Core.DISET.private.Protocols import gProtocolDict
from DIRAC.Core.DISET.private.Dispatcher import Dispatcher
from DIRAC.Core.DISET.private.GatewayDispatcher import GatewayDispatcher
from DIRAC.Core.DISET.private.ServiceConfiguration import ServiceConfiguration
from DIRAC.Core.Utilities.ThreadPool import ThreadPool
from DIRAC.Core.Utilities.ReturnValues import S_OK, S_ERROR
from DIRAC.Core.Utilities.Subprocess import shellCall
from DIRAC.Core.Utilities import Network, Time
from DIRAC.Core.Utilities.ThreadScheduler import gThreadScheduler
from DIRAC.LoggingSystem.Client.Logger import gLogger
from DIRAC.MonitoringSystem.Client.MonitoringClient import gMonitor
from DIRAC.Core.Security import CS

class Server:

  bAllowReuseAddress = True
  iListenQueueSize = 5
  __memScale = {'kB': 1024.0, 'mB': 1024.0*1024.0, 'KB': 1024.0, 'MB': 1024.0*1024.0}

  def __init__( self, serviceName ):
    """
    Constructor

    @type serviceName: string
    @param serviceName: Name of the starting service
    """
    gLogger.always( "Starting service %s" % serviceName )
    while serviceName[0] == "/":
      serviceName = serviceName[1:]
    self.serviceName = serviceName
    self.startTime = Time.dateTime()
    serviceCfg = ServiceConfiguration( serviceName )
    self.__buildURL( serviceCfg )
    self.__initializeMonitor( serviceCfg )
    self.servicesList = [ serviceCfg ]
    if serviceName == GatewayDispatcher.gatewayServiceName:
      self.handlerManager = GatewayDispatcher( self.servicesList )
    else:
      self.handlerManager = Dispatcher( self.servicesList )
    retDict = self.handlerManager.loadHandlers()
    if not retDict[ 'OK' ]:
      gLogger.fatal( "Error while loading handler", retDict[ 'Message' ] )
      sys.exit(1)
    self.handlerManager.initializeHandlers()
    maxThreads = 0
    for serviceCfg in self.servicesList:
      self.__initializeTransport( serviceCfg )
      maxThreads = max( maxThreads, serviceCfg.getMaxThreads() )
    self.threadPool = ThreadPool( 1, maxThreads )
    self.threadPool.daemonize()
    self.__monitorLastStatsUpdate = time.time()

  def __initializeMonitor( self, serviceCfg ):
    gMonitor.setComponentType( gMonitor.COMPONENT_SERVICE )
    gMonitor.setComponentName( serviceCfg.getName() )
    gMonitor.setComponentLocation( serviceCfg.getURL() )
    gMonitor.initialize()
    gMonitor.registerActivity( "Queries", "Queries served", "Framework", "queries", gMonitor.OP_RATE )
    gMonitor.registerActivity( 'CPU', "CPU Usage", 'Framework', "CPU,%", gMonitor.OP_MEAN, 600 )
    gMonitor.registerActivity( 'MEM', "Memory Usage", 'Framework', 'Memory,MB', gMonitor.OP_MEAN, 600 )
    gMonitor.registerActivity( 'PendingQueries', "Pending queries", 'Framework', 'queries', gMonitor.OP_MEAN )
    gMonitor.registerActivity( 'ActiveQueries', "Active queries", 'Framework', 'threads', gMonitor.OP_MEAN )
    gThreadScheduler.addPeriodicTask( 30, self.__reportThreadPoolContents )

  def __reportThreadPoolContents( self ):
    gMonitor.addMark( 'PendingQueries', self.threadPool.pendingJobs() )
    gMonitor.addMark( 'ActiveQueries', self.threadPool.numWorkingThreads() )

  def __VmB(self, VmKey):
      '''Private.
      '''
      procFile = '/proc/%d/status' % os.getpid()
       # get pseudo file  /proc/<pid>/status
      try:
          t = open( procFile )
          v = t.read()
          t.close()
      except:
          return 0.0  # non-Linux?
       # get VmKey line e.g. 'VmRSS:  9999  kB\n ...'
      i = v.index( VmKey )
      v = v[i:].split(None, 3)  # whitespace
      if len(v) < 3:
          return 0.0  # invalid format?
       # convert Vm value to bytes
      return float(v[1]) * self.__memScale[v[2]]

  def __startReportToMonitoring(self):
    gMonitor.addMark( "Queries" )
    now = time.time()
    stats = os.times()
    cpuTime = stats[0] + stats[2]
    if now - self.__monitorLastStatsUpdate < 10:
      return ( now, cpuTime )
    # Send CPU consumption mark
    wallClock = now - self.__monitorLastStatsUpdate
    self.__monitorLastStatsUpdate = now
    # Send Memory consumption mark
    membytes = self.__VmB('VmRSS:')
    if membytes:
      mem = membytes / ( 1024. * 1024. )
      gMonitor.addMark('MEM', mem )
    return( now, cpuTime )

  def __endReportToMonitoring( self, initialWallTime, initialCPUTime ):
    wallTime = time.time() - initialWallTime
    stats = os.times()
    cpuTime = stats[0] + stats[2] - initialCPUTime
    percentage = cpuTime / wallTime * 100.
    if percentage > 0:
      gMonitor.addMark( 'CPU', percentage )

  def __buildURL( self, serviceCfg ):
    """
    Build the service URL
    """
    protocol = serviceCfg.getProtocol()
    serviceURL = serviceCfg.getURL()
    if serviceURL:
        if serviceURL.find( protocol ) != 0:
          urlFields = serviceURL.split( ":" )
          urlFields[0] = protocol
          self.serviceURL = ":".join( urlFields )
          serviceCfg.setURL( serviceURL )
        return
    hostName = serviceCfg.getHostname()
    port = serviceCfg.getPort()
    sURL = "%s://%s:%s/%s" % ( protocol,
                                  hostName,
                                  port,
                                  serviceCfg.getName() )
    if sURL[-1] == "/":
      sURL = sURL[:-1]
    serviceCfg.setURL( sURL )

  def __initializeTransport( self, serviceCfg ):
    """
    Initialize the transport
    """
    transportArgs = {}
    transportExtraKeywords = [ "SSLSessionTimeout" ]
    for kw in transportExtraKeywords:
      value = serviceCfg.getOption( kw )
      if value:
        transportArgs[ kw ] = value
    protocol = serviceCfg.getProtocol()
    if protocol in gProtocolDict.keys():
      gLogger.verbose( "Initializing %s transport" % protocol, serviceCfg.getURL() )
      from DIRAC.Core.DISET.private.Transports.PlainTransport import PlainTransport
      self.transport = gProtocolDict[ protocol ][ 'transport' ]( ( "", serviceCfg.getPort() ),
                            bServerMode = True, **transportArgs )
      retVal = self.transport.initAsServer()
      if not retVal[ 'OK' ]:
        gLogger.fatal( "Cannot start listening connection", retVal[ 'Message' ] )
        sys.exit(1)
    else:
      gLogger.fatal( "No valid protocol specified for the service", "%s is not a valid protocol" % sProtocol )
      sys.exit(1)

  def serve( self ):
    """
    Start serving petitions
    """
    for serviceCfg in self.servicesList:
      gLogger.info( "Handler up", "Serving from %s" % serviceCfg.getURL() )
    while True:
      self.__handleRequest()
      #self.threadPool.processResults()

  def __handleRequest( self ):
    """
    Handle an incoming request
    """
    try:
      inList = [ self.transport.getSocket() ]
      inList, outList, exList = select.select( inList, [], [], 10 )
      if len( inList ) != 1:
        return
      retVal = self.transport.acceptConnection()
      if not retVal[ 'OK' ]:
        gLogger.warn( "Error while accepting a connection: ", retVal[ 'Message' ])
        return
      clientTransport = retVal[ 'Value' ]
    except socket.error:
      return
    clientIP = clientTransport.getRemoteAddress()[0]
    if clientTransport.getRemoteAddress()[0] in CS.getBannedIPs():
      gLogger.warn( "Client connected from banned ip %s" % clientIP )
    else:
      self.threadPool.generateJobAndQueueIt( self.processClient,
                                      args = ( clientTransport, ),
                                      oExceptionCallback = self.processClientException )

  def processClientException( self, threadedJob, exceptionInfo ):
    """
    Process an exception generated in a petition
    """
    gLogger.exception( "Exception in thread", lException = exceptionInfo )

  def processClient( self, clientTransport ):
    """
    Receive an action petition and process it from the client
    """
    try:
      cpuStats = self.__startReportToMonitoring()
    except:
      cpuStats = False
    try:
      gLogger.verbose( "Incoming connection from %s" % clientTransport.getRemoteAddress()[0] )
      clientTransport.handshake()
      self.handlerManager.processClient( clientTransport )
    finally:
      clientTransport.close()
      if cpuStats:
        self.__endReportToMonitoring( *cpuStats )
