# **************************************************************************
# *
# * Authors:    Laura del Cano (ldelcano@cnb.csic.es)
# *
# * Unidad de  Bioinformatica of Centro Nacional de Biotecnologia , CSIC
# *
# * This program is free software; you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation; either version 2 of the License, or
# * (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA
# * 02111-1307  USA
# *
# *  All comments concerning this program package may be sent to the
# *  e-mail address 'jmdelarosa@cnb.csic.es'
# *
# **************************************************************************

import unittest, sys
from pyworkflow.em import *
from pyworkflow.tests import *


# Some utility functions to import micrographs that are used
# in several tests.
class TestRelionBase(BaseTest):
    @classmethod
    def setData(cls, dataProject='xmipp_tutorial'):
        cls.dataset = DataSet.getDataSet(dataProject)
        cls.particlesFn = cls.dataset.getFile('particles')
        cls.vol = cls.dataset.getFile('volumes')
    
    @classmethod
    def runImportParticles(cls, pattern, samplingRate, checkStack=False):
        """ Run an Import particles protocol. """
        protImport = cls.newProtocol(ProtImportParticles, 
                                      pattern=pattern, samplingRate=samplingRate, 
                                      checkStack=checkStack)
        cls.launchProtocol(protImport)
        # check that input images have been imported (a better way to do this?)
        if protImport.outputParticles is None:
            raise Exception('Import of images: %s, failed. outputParticles is None.' % pattern)
        return protImport
    
    @classmethod
    def runNormalizeParticles(cls, particles):
        """ Run normalize particles protocol """
        protPreproc = cls.newProtocol(XmippProtPreprocessParticles,
                                      doNormalize=True)
        protPreproc.inputParticles.set(particles)
        cls.launchProtocol(protPreproc)
        return protPreproc
    
    @classmethod
    def runImportVolumes(cls, pattern, samplingRate):
        """ Run an Import particles protocol. """
        protImport = cls.newProtocol(ProtImportVolumes, 
                                     pattern=pattern, samplingRate=samplingRate)
        cls.launchProtocol(protImport)
        return protImport


class TestRelionClassify2D(TestRelionBase):
    @classmethod
    def setUpClass(cls):
        setupTestProject(cls)
        TestRelionBase.setData('mda')
        cls.protImport = cls.runImportParticles(cls.particlesFn, 3.5)
        cls.protNormalize = cls.runNormalizeParticles(cls.protImport.outputParticles)
            
    def testRelion2D(self):                  
        print "Run relion2D"
        prot2D = self.newProtocol(ProtRelionClassify2D,
                                  doCTF=False, maskRadiusA=170,
                                  numberOfMpi=4, numberOfThreads=1)
        prot2D.numberOfClasses.set(4)
        prot2D.numberOfIterations.set(3)
        prot2D.inputParticles.set(self.protNormalize.outputParticles)
        self.launchProtocol(prot2D)        
        
        self.assertIsNotNone(getattr(prot2D, 'outputClasses', None), 
                             "There was a problem with Relion 2D:\n" + prot2D.getErrorMessage()) 


class TestRelionClassify3D(TestRelionBase):
    @classmethod
    def setUpClass(cls):
        setupTestProject(cls)
        TestRelionBase.setData('mda')
        cls.protImport = cls.runImportParticles(cls.particlesFn, 3.5)
#         cls.protNormalize = cls.runNormalizeParticles(cls.protImport.outputParticles)
        cls.protImportVol = cls.runImportVolumes(cls.vol, 3.5)
    
    def testProtRelionClassify3D(self):
        relionNormalize = self.newProtocol(ProtRelionPreprocessParticles)
        relionNormalize.inputParticles.set(self.protImport.outputParticles)
        relionNormalize.doNormalize.set(True)
        self.launchProtocol(relionNormalize)

        print "Run ProtRelionClassify3D"
        relion3DClass = self.newProtocol(ProtRelionClassify3D, 
                                         numberOfClasses=3, numberOfIterations=4, 
                                         doCTF=False, runMode=1,
                                         numberOfMpi=2, numberOfThreads=2)
        relion3DClass.inputParticles.set(relionNormalize.outputParticles)
        relion3DClass.referenceVolume.set(self.protImportVol.outputVolume)
        self.launchProtocol(relion3DClass)
        
        self.assertIsNotNone(getattr(relion3DClass, 'outputClasses', None), 
                             "There was a problem with Relion 3D:\n" + relion3DClass.getErrorMessage()) 


class TestRelionImport(BaseTest):
    
    @classmethod
    def setUpClass(cls):
        setupTestProject(cls)
        cls.ds = DataSet.getDataSet('relion_tutorial')
 
    def runImport(self, starFile, samplingRate):
        relionImport = self.newProtocol(ProtRelionImport, 
                                         inputStar=starFile, samplingRate=samplingRate) 
        self.launchProtocol(relionImport)
        
        self.assertIsNotNone(getattr(relionImport, 'outputClasses', None), 
                             "There was a problem with Relion 3D:\n" + relionImport.getErrorMessage())
        
        return relionImport 
        
    def test1(self):
        """ Firt try to import from an star file with not micrograph id
        and not binaries files.
        """
        self.runImport(starFile=self.ds.getFile('import1_data_star'), samplingRate=2.53)
        
    def test2(self):
        """ Firt try to import from an star file with not micrograph id
        and not binaries files.
        """
        relionImport = self.runImport(starFile=self.ds.getFile('import2_data_star'), samplingRate=2.53)     
        
        # Test now a reconstruction after the imported particles   
        relionReconstruct = self.newProtocol(ProtRelionReconstruct)
        relionReconstruct.inputParticles.set(relionImport.outputParticles)
        self.launchProtocol(relionReconstruct)
        
class TestRelionPreprocess(TestRelionBase):
    """ This class helps to test all different preprocessing particles options on RElion. """
    @classmethod
    def setUpClass(cls):
        setupTestProject(cls)
        TestRelionBase.setData('mda')
        cls.protImport = cls.runImportParticles(cls.particlesFn, 3.5)
            
    def testNormalize(self):
        """ Normalize particles.
        """
       
        # Test now a normalization after the imported particles   
        relionNormalize = self.newProtocol(ProtRelionPreprocessParticles)
        relionNormalize.inputParticles.set(self.protImport.outputParticles)
        relionNormalize.doNormalize.set(True)
        relionNormalize.backRadius.set(40)
        self.launchProtocol(relionNormalize)

    def testAllOptions(self):
        """ Test all options at once.
        """
       
        # Test now a normalization after the imported particles   
        relionNormalize = self.newProtocol(ProtRelionPreprocessParticles)
        relionNormalize.inputParticles.set(self.protImport.outputParticles)
        relionNormalize.doNormalize.set(True)
        relionNormalize.backRadius.set(40)
        relionNormalize.doScale.set(True)
        relionNormalize.scaleSize.set(24)
        relionNormalize.doWindow.set(True)
        relionNormalize.windowSize.set(120)
        relionNormalize.doInvert.set(True)
        relionNormalize.doRemoveDust.set(True)
        relionNormalize.whiteDust.set(4)
        relionNormalize.blackDust.set(8)
        
        self.launchProtocol(relionNormalize)       
