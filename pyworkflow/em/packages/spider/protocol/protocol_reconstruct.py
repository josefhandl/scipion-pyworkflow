# **************************************************************************
# *
# * Authors:     J.M. De la Rosa Trevin (jmdelarosa@cnb.csic.es)
#                Tapu Shaikh            (shaikh@ceitec.muni.cz)
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
# *  e-mail address 'scipion@cnb.csic.es'
# *
# **************************************************************************
"""
"""

import pyworkflow.em as em
import pyworkflow.protocol.params as params
from pyworkflow.em.protocol import ProtRefine3D
from pyworkflow.em.constants import ALIGN_PROJ
from pyworkflow.em.data import Volume

from ..spider import SpiderDocFile, writeScript, runScript, getScript
from ..convert import ANGLE_PHI, ANGLE_PSI, ANGLE_THE, SHIFTX, SHIFTY, convertEndian, alignmentToRow
from protocol_base import SpiderProtocol



                               
class SpiderProtReconstruct(ProtRefine3D, SpiderProtocol):
    """
    Simple reconstruction protocol using Fourier back projection.
    Uses Spider BP 32F program.
    Mainly used for testing conversion of Euler angles.
    """
    _label = 'reconstruct fourier'
    
    #--------------------------- DEFINE param functions --------------------------------------------   
    def _defineParams(self, form):
        form.addSection(label='Input')

        form.addParam('inputParticles', params.PointerParam, 
                      pointerClass='SetOfParticles', 
                      pointerCondition='hasAlignmentProj',
                      label="Input particles", important=True,
                      help='Select the input particles.\n')
        form.addParallelSection(threads=1, mpi=0)
        
    #--------------------------- INSERT steps functions --------------------------------------------  
    def _insertAllSteps(self):        
        # Create new stacks and selfiles per defocus groups
        self._insertFunctionStep('convertInputStep', self.inputParticles.get().getObjId())

        self._insertFunctionStep('runScriptStep', 'recons_fourier.txt')
                
        self._insertFunctionStep('createOutputStep')
    
    #--------------------------- STEPS functions --------------------------------------------
    
    def convertInputStep(self, particlesId):
        """ Convert all needed inputs before running the refinement script. """
        partSet = self.inputParticles.get()

        ih = em.ImageHandler()

        stackfile = self._getPath('particles.stk')
        docfile = self._getPath('docfile.stk')
        doc = SpiderDocFile(docfile, 'w+')
        now = 'now' #FIXME
        doc.writeComment("spi/dat   Generated by Scipion on %s" % now)
        doc.writeComment("  KEY       PSI,    THE,    PHI,   REF#,    EXP#,  CUM.{ROT,   SX,    SY},  NPROJ,   DIFF,      CCROT,    ROT,     SX,     SY,   MIR-CC")
        
        
        for i, img in enumerate(partSet):
            
            ind = i + 1
            ih.convert(img, (ind, stackfile))
            alignRow = {ANGLE_PSI: 0.,
                        ANGLE_THE: 0.,
                        ANGLE_PHI: 0.,
                        SHIFTX: 0.,
                        SHIFTY: 0.}
            alignment = img.getTransform()
            
            if alignment is not None:
                alignmentToRow(alignment, alignRow, ALIGN_PROJ)
                
            values = [0.00, alignRow[ANGLE_THE], alignRow[ANGLE_PHI], 
                      0.00, ind, 
                      alignRow[ANGLE_PSI], alignRow[SHIFTX], alignRow[SHIFTY], 
                      0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.0]
            doc.writeValues(*values)  
            
        convertEndian(stackfile, partSet.getSize())  
                
    def runScriptStep(self, script):
        params = {'[unaligned_images]': "'particles'",
                  '[next_group_align]': "'docfile'",
                  '[nummps]': self.numberOfThreads.get()
                  }     
        writeScript(script, self._getPath('recons_fourier.txt'), params)
        runScript(script, 'txt/stk', log=self._log,
                         cwd=self.getWorkingDir())
        
    def createOutputStep(self):
        imgSet = self.inputParticles.get()
        vol = Volume()
        # FIXME: return two half-volumes as well
        vol.setFileName(self._getPath('volume.stk'))
        vol.setSamplingRate(imgSet.getSamplingRate())

        self._defineOutputs(outputVolume=vol)
        self._defineSourceRelation(self.inputParticles, vol)
    
    #--------------------------- INFO functions -------------------------------------------- 
    def _validate(self):
        errors = []
        return errors
    
    def _summary(self):
        summary = []
        return summary
    
    #--------------------------- UTILS functions --------------------------------------------

