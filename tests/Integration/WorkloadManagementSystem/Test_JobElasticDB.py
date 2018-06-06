""" This tests only need the JobElasticDB, and connects directly to it
"""

import unittest

from DIRAC.Core.Base.Script import parseCommandLine
parseCommandLine()

from DIRAC import gLogger
from DIRAC.WorkloadManagementSystem.DB.JobElasticDB import JobDB


class JobDBTestCase(unittest.TestCase):
  """ Base class for the JobElasticDB test cases
  """

  def setUp(self):
    gLogger.setLevel('DEBUG')
    self.jobDB = JobDB()

  def tearDown(self):
    self.jobDB = False


class JobParametersCase(JobDBTestCase):
  """  TestJobElasticDB represents a test suite for the JobElasticDB database front-end
  """

  def test_setAndGetJobFromDB(self):
    """
    test_setAndGetJobFromDB tests the functions setJobParameter and getJobParameters in
    WorkloadManagementSystem/DB/JobElasticDB.py

    Test Values:

    100: JobID (int)
    DIRAC: Name (basestring)
    dirac@cern: Value (basestring)
    """
    res = self.jobDB.setJobParameter(100, 'DIRAC', 'dirac@cern')
    self.assertTrue(res['OK'])
    res = self.jobDB.getJobParameters(100)
    self.assertTrue(res['OK'])
    self.assertEqual(res['Value']['DIRAC'], 'dirac@cern')


if __name__ == '__main__':

  suite = unittest.defaultTestLoader.loadTestsFromTestCase(JobParametersCase)
  testResult = unittest.TextTestRunner(verbosity=2).run(suite)
