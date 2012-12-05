#!/usr/bin/python

import optparse, os, sys
from DataExtractor import *
from DataParser import *
# Common variables...
from PYTHON_GLOBALS import *

usage="%prog [options]"
description="""Main driver to get data needed out of the KBase and store it locally.
Data will be stored in a local database autoReconInfo
All of this data is QUERY INDEPENDENT. It should all be the same
for any organism for which you want to do a reconstruction..."""
parser = optparse.OptionParser(usage=usage, description=description)
parser.add_option("-r", "--regenerate", help="Regenerate database if it already exists (NOTE - takes a long time)", action="store_true", dest="regenerate", default=False)
parser.add_option("-d", "--deleteonly", help="Delete data files but do not regenerate (WARNING - this is not reversible)", action="store_true", dest="delete", default=False)
parser.add_option("-v", "--verbose", help="Display all WARNINGS (D: Only display messages related to completeness)", action="store_true", dest="verbose", default=False)
parser.add_option("-f", "--folder", help="Base directory (folder) in which all of the data files are to be stored", action="store", dest="folder", default=None)
(options, args) = parser.parse_args()

if options.folder is None:
    sys.stderr.write("ERROR: In ExtractorDriver.py - folder (-f) is a required argument\n")
    exit(2)

def safeRemove(fname, dirname):
    totalfname = os.path.join(dirname, fname)
    try:
        # Check for file existence
        fid = open(totalfname, "r")
        fid.close()
        os.remove(totalfname)
    # If there is still an OSError despite the file existing we want to raise that, it will probably
    # cause problems trying to write to the files anyway. but an IOError just means the file isn't there.
    except IOError:
        pass

if options.regenerate or options.delete:
    safeRemove(OTU_ID_FILE, options.folder)
    safeRemove(SUBSYSTEM_FID_FILE, options.folder)
    safeRemove(DLIT_FID_FILE, options.folder)
    safeRemove(CONCATINATED_FID_FILE, options.folder)
    safeRemove(SUBSYSTEM_OTU_FIDS_FILE, options.folder)
    safeRemove(SUBSYSTEM_OTU_FID_ROLES_FILE, options.folder)
    safeRemove(SUBSYSTEM_OTU_FASTA_FILE, options.folder)
    safeRemove(SUBSYSTEM_OTU_FASTA_FILE + ".psq", options.folder) 
    safeRemove(SUBSYSTEM_OTU_FASTA_FILE + ".pin", options.folder)
    safeRemove(SUBSYSTEM_OTU_FASTA_FILE + ".phr", options.folder)
    safeRemove(OTU_NEIGHBORHOOD_FILE, options.folder)
    safeRemove(COMPLEXES_ROLES_FILE, options.folder)
    safeRemove(REACTION_COMPLEXES_FILE, options.folder)

#    folder = os.path.join("data", "OTU")
#    for the_file in os.listdir(folder):
#        file_path = os.path.join(folder, the_file)
#        if os.path.isfile(file_path):
#            os.unlink(file_path)

# Our job is done if all we want to do is delete files.
if options.delete:
    exit(0)

sys.stderr.write("Generating requested data:....\n")

############
# Get lists of OTUs
############
sys.stderr.write("OTU data...\n")
try:
    otus, prokotus = readOtuData(options.folder)
except IOError:
    otus, prokotus = getOtuGenomeIds(MINN, COUNT)
#    otus, prokotus = getOtuGenomeIds(MINN, 1200)
    writeOtuData(otus, prokotus, options.folder)

############
# Get a list of subsystem FIDs
############
sys.stderr.write("List of subsystem FIDS...\n")
try:
    sub_fids = readSubsystemFids(options.folder)
except IOError:
    sub_fids = subsystemFids(MINN, COUNT)
    # NOTE - This is a TEMPORARY workaround for an issue with
    # the KBase subsystem load. This function WILL BE DELETED
    # and reverted to the call above once that issue is fixed...
#    sub_fids = subsystemFids_WORKAROUND(MINN, COUNT)
    writeSubsystemFids(sub_fids, options.folder)

###########
# ALso get a list of Dlit FIDs
# We include these because having them
# greatly expands the number of roles for which we
# have representatives.
##########
sys.stderr.write("Getting a list of DLit FIDs...\n")
try:
    dlit_fids = readDlitFids(options.folder)
except IOError:
    dlit_fids = getDlitFids(MINN, COUNT)
    writeDlitFids(dlit_fids, options.folder)

##########
# Concatinate the two FID lists before filtering
# (Note - doing so after would be possible as well but
# can lead to the same kinds of biases as not filtering
# the subsystems... Im not sure the problem would
# be as bad for these though)
##########
sys.stderr.write("Combining lists of subsystem and DLit FIDS...\n")
fn = os.path.join(options.folder, CONCATINATED_FID_FILE)
try:
    all_fids = set()
    for line in open(fn, "r"):
        all_fids.add(line.strip("\r\n"))
    all_fids = list(all_fids)
except IOError:
    all_fids = list(set(sub_fids + dlit_fids))
    f = open(fn, "w")
    for fid in all_fids:
        f.write("%s\n" %(fid))
    f.close()

#############
# Filter the subsystem FIDs by organism... we only want OTU genes.
# Unlike the neighborhood analysis, we don't want to include only 
# prokaryotes here.
#############
sys.stderr.write("Filtered list by OTUs...\n")
try:
    otu_fids = readFilteredOtus(options.folder)
except IOError:
    otu_fids = filterFidsByOtus(all_fids, otus)
    writeFilteredOtus(otu_fids, options.folder)

#############
# Identify roles for the OTU genes in the organism...
#############
sys.stderr.write("Roles for filtered list...\n")
try:
    otu_fidsToRoles = readFilteredOtuRoles(options.folder)
except IOError:
    otu_fidsToRoles, otuRolesToFids = fidsToRoles(otu_fids)
    writeFilteredOtuRoles(otu_fidsToRoles, options.folder)

#############
# Generate a FASTA file
# for the fids in fidsToRoles
#############
sys.stderr.write("Subsystem FASTA file...\n")
try:
    readSubsystemFasta(options.folder)
except IOError:
    fidsToSeqs = fidsToSequences(otu_fidsToRoles.keys())
    writeSubsystemFasta(fidsToSeqs, options.folder)

#############
# Get neighborhood info
# for the OTUs (prokaryote only because neighborhoods 
# are generally not conserved for eukaryotes)
#############
#sys.stderr.write("OTU neighborhoods...\n")
#try:
#    fid = open(OTU_NEIGHBORHOOD_FILE, "r")
#    fid.close()
#except IOError:
    # tuplist: [ (contig_id, feature_id, start_location, strand) ]
    # Final file has this and then the roles in a delimited list
#    for prokotu in prokotus:
        # This is mostly because I keep running into incredibly stupid errors.
        # Lets see if I can figure out what the hell is causing them.
#        try:
#            fid = open(os.path.join("data", "OTU", prokotu), "r")
#            fid.close()
#        except IOError:
#            tuplist, fidToRoles = getGenomeNeighborhoodsAndRoles([prokotu])
#            writeOtuNeighborhoods(tuplist, fidToRoles, options.verbose, os.path.join("data", "OTU", prokotu))
#    cmd = "cat %s%s* > %s" %(os.path.join("data", "OTU"), os.sep, OTU_NEIGHBORHOOD_FILE)
#    os.system(cmd)

################
# Complexes --> Roles
# Needed to go from annotation likelihoods
# to reaction likelihoods
# Note that it is easier to go in this direction 
#    Because we need all the roles in a complex to get the probability of that complex.
#
################
sys.stderr.write("Complexes to roles...\n")
try:
    complexToRequiredRoles = readComplexRoles(options.folder)
except IOError:
    complexToRequiredRoles, requiredRolesToComplexes = complexRoleLinks(MINN, COUNT)
    writeComplexRoles(complexToRequiredRoles, options.folder)

########
# reaction --> complex
# Again it is easier to go in this direction since we'll be filtering multiple complexes down to a single reaction.
#######

sys.stderr.write("Reactions to complexes...\n")
try:
    rxnToComplexes = readReactionComplex(options.folder)
except IOError:
    rxnToComplexes, complexesToReactions = reactionComplexLinks(MINN, COUNT)
    writeReactionComplex(rxnToComplexes, options.folder)

sys.stderr.write("Data gathering done...\n")
