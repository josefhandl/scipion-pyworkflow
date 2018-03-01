# coding: latin-1
# **************************************************************************
# *
# * Authors:    Carlos Oscar Sorzano (coss@cnb.csic.es)
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
Bibtex string file for Powerfit package.
"""

_bibtexStr = """


@article{vanZundert2015,
title = "Fast and sensitive rigid-body fitting into cryo-EM density maps with PowerFit",
journal = "AIMS Biophysics",
volume = "2",
pages = "73 - 87",
year = "2015",
doi = "http://dx.doi.org/10.3934/biophy.2015.2.73",
url = "http://www.aimspress.com/article/10.3934/biophy.2015.2.73/pdf",
author = "van Zundert, G. C. P.  and Bonvin, A. M. J. J.",
keywords = "Rigid fitting"
}
"""

from pyworkflow.utils import parseBibTex

_bibtex = parseBibTex(_bibtexStr)  
