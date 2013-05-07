#!/usr/bin/python

import os, sys

try:
    import json
except ImportError:
    # It is assumed that the user has simplejson if < python 2.6
    sys.path.append('simplejson-2.3.3')
    import simplejson as json

from CDMI import CDMI_API, CDMI_EntityAPI
from DataExtractor import *
from DataParser import *
from PYTHON_GLOBALS import *

# These two functions could be collapsed into one that gets the genome from the workspace
# and builds the fasta file.

def GenomeJsonToFasta(outputbase, organismid):
    '''Convert a genome JSON object into an amino-acid FASTA file (for BLAST purposes)'''
    genome_json_file = os.path.join(outputbase, organismid, "%s.json" %(organismid))
    # If the fasta file already exists dont bother re-creating it.
    fasta_file = os.path.join(outputbase, organismid, "%s.faa" %(organismid))
    try:
        fid = open(fasta_file, "r")
        fid.close()
        return fasta_file
    except IOError:
        pass

    resp = json.load(open(genome_json_file, "r"))
    fout = open(fasta_file, "w")
    features = resp["features"]
    for feature in features:
        # Not a protein-encoding gene
        if "protein_translation" not in feature:
            continue
        myid = feature["id"]
        if "function" in feature:
            function = feature["function"]
        else:
            function = ""
        seq = feature["protein_translation"]
        fout.write(">%s %s\n%s\n" %(myid, function, seq))
    return fasta_file

# Organismid is a KBase genome id for the organism
def setUpQueryData(outputbase, organismid):
    '''Create a place for the probability calculation results and move in the
    genome JSON file, then make the call to get a FASTA file ot of it'''
    sys.stderr.write("Creating output directory for organism %s...\n" %(organismid))
    try:
        os.mkdir(organismid)
        sys.stderr.write("Output folder for organism ID %s created with the same name\n" %(organismid) )
    except OSError:
        sys.stderr.write("Output folder %s already exists.\n" %(organismid))
        pass
    sys.stderr.write("Creating a genome JSON file for organism %s...\n" %(organismid))

    json_file = os.path.join(outputbase, organismid, "%s.json" %(organismid))
    try:
        # As a workaround for now, I should create a JSON file before-hand with the appropriate name and then this check will pass.
        fid = open(json_file, "r")
        fid.close()
        sys.stderr.write("JSON file %s already exists.\n" %(organismid))
    except IOError:
        sys.stderr.write("INTERNAL ERROR: JSON file %s does not exist but should have been created by the perl script calling this function...\n" %(json_file))
        exit(2)
    fasta_file = GenomeJsonToFasta(outputbase, organismid)
    return fasta_file, json_file

def runBlast(outputbase, organismid, query_fasta, folder):
    '''A simplistic wrapper to BLAST the query proteins against the
    subsystem proteins'''
    blast_result_file = os.path.join(outputbase, organismid, "%s.blastout" %(organismid))
    try:
        fid = open(blast_result_file, "r")
        fid.close()
        sys.stderr.write("BLAST results file %s already exists.\n" %(blast_result_file))
    except IOError:
        cmd = "blastp -query \"%s\" -db %s -outfmt 6 -evalue 1E-5 -num_threads 1 -out \"%s\"" %(query_fasta, os.path.join(folder, SUBSYSTEM_OTU_FASTA_FILE), blast_result_file)
        sys.stderr.write("Running BLAST with command: %s\n" %(cmd))
        os.system(cmd)
        sys.stderr.write("BLAST command complete\n")
    return blast_result_file

def RolesetProbabilitiesMarble(outputbase, organismid, blast_result_file, folder):
    '''Calculate the probabilities of rolesets (i.e. each possible combination of
    roles implied by the functions of the proteins in subsystems) from the BLAST results.

   Returns a file with three columns(query, rolestring, probability)  
   rolestring = "\\\" separating all roles of a protein with a single function (order does not matter)
    '''
    roleset_probability_file = os.path.join(outputbase, organismid, "%s.rolesetprobs" %(organismid))
    try:
        fid = open(roleset_probability_file, "r")
        fid.close()
        sys.stderr.write("Roleset probability file %s already exists\n" %(roleset_probability_file))
        return roleset_probability_file
    except IOError:
        pass
    sys.stderr.write("Performing marble-picking on rolesets...")

    # Read in the target roles (this function returns the roles as lists!)
    targetIdToRole, targetRoleToId = readFilteredOtuRoles(folder)

    # Convert the lists of roles into "rolestrings" (sort the list so that order doesn't matter)
    # in order to deal with the case where some of the hits are multi-functional and others only have
    # a single function...
    targetIdToRoleString = {}
    for target in targetIdToRole:
        stri = SEPARATOR.join(sorted(targetIdToRole[target]))
        targetIdToRoleString[target] = stri

    # Query --> [ (target1, score 1), (target 2, score 2), ... ]
    idToTargetList = parseBlastOutput(blast_result_file)

    # This is a holder for all of our results
    # It is a list of tuples in the form (query, rolestring, probability)
    rolestringTuples = []
    # For each query gene we calcualte the likelihood of each possible rolestring.
    for query in idToTargetList:
        # First we need to know the maximum score
        # I have no idea why but I'm pretty sure Python is silently turning the second element of these tuples
        # into strings.
        #
        # That's why I turn them back...
        maxscore = 0
        for tup in idToTargetList[query]:
            if float(tup[1]) > maxscore:
                maxscore = float(tup[1])

        # Now we calculate the cumulative squared scores
        # for each possible rolestring. This along with PC*maxscore is equivalent
        # to multiplying all scores by themselves and then dividing by the max score.
        # This is done to avoid some pathological cases and give more weight to higher-scoring hits
        # and not let much lower-scoring hits \ noise drown them out.
        rolestringToScore = {}
        for tup in idToTargetList[query]:
            try:
                rolestring = targetIdToRoleString[tup[0]]
            except KeyError:
#                sys.stderr.write("ERROR: Target ID %s from the BLAST file had no roles in the rolestring dictionary??\n" %(tup[0]))
                continue
            if rolestring in rolestringToScore:
                rolestringToScore[rolestring] += (float(tup[1]) ** 2)
            else:
                rolestringToScore[rolestring] = (float(tup[1])**2)

        # Now lets iterate over all of them and calculate the probability
        # Probability = sum(S(X)^2)/(sum(S(X)^2 + PC*maxscore))
        # Lets get the denominator first
        denom = PSEUDOCOUNT*maxscore
        for stri in rolestringToScore:
            denom += rolestringToScore[stri]

        # Now the numerators, which are different for every rolestring
        for stri in rolestringToScore:
            p = rolestringToScore[stri]/denom
            rolestringTuples.append( (query, stri, p) )

    fid = open(roleset_probability_file, "w")
    for p in rolestringTuples:
        fid.write("%s\t%s\t%1.4f\n" %(p[0], p[1], p[2]))
    fid.close()
    sys.stderr.write("done\n")
    return roleset_probability_file
        

def RolesetProbabilitiesToRoleProbabilities(outputbase, organismid, roleset_probability_file):
    '''Compute probability of each role from the rolesets for each query protein.
    At the moment the strategy is to take any set of rolestrings containing the same roles
    And add their probabilities.
    So if we have hits to both a bifunctional enzyme with R1 and R2, and
    hits to a monofunctional enzyme with only R1, R1 ends up with a greater
    probability than R2.

    I had tried to normalize to the previous sum but I need to be more careful than that
    (I'll put it on my TODO list) because if you have e.g.
    one hit to R1R2 and one hit to R3 then the probability of R1 and R2 will be unfairly
    brought down due to the normalization scheme...

    Returns a file with three columns: Query gene ID, role, and probability
    '''
    role_probability_file = os.path.join(outputbase, organismid, "%s.roleprobs" %(organismid))
    try:
        fid = open(role_probability_file, "r")
        fid.close()
        sys.stderr.write("Role probability file %s already exists\n" %(role_probability_file))
        return role_probability_file
    except IOError:
        pass
    sys.stderr.write("Generating role probabilities from roleset probabilities...")

    # roleset_probability_file - original file that we want to read.
    # Query --> list of (rolelist, probability)
    queryToTuplist = readRolesetProbabilityFile(roleset_probability_file)

    fid = open(role_probability_file, "w")
    for query in queryToTuplist:
        # Get the total probability - needed for re-normalization later.
        totalp = 0
        for tup in queryToTuplist[query]:
            totalp += tup[1]

        # This section actually does the convertsion of probabilities.
        queryRolesToProbs = {}
        for tup in queryToTuplist[query]:
            rolelist = tup[0].split(SEPARATOR)
            # Add up all the instances of each particular role on the list.
            for role in rolelist:
                if role in queryRolesToProbs:
                    queryRolesToProbs[role] += tup[1]
                else:
                    queryRolesToProbs[role] = tup[1]
        rolesum = 0

        # Write them to a file.
        for role in queryRolesToProbs:
            fid.write("%s\t%s\t%s\n" %(query, role, queryRolesToProbs[role]))
    fid.close()

    sys.stderr.write("done\n")
    return role_probability_file

# For now to get the probability I just assign this as the MAXIMUM for each role
# to avoid diluting out by noise.
#
# The gene assignments are all genes within DILUTION_PERCENT of the maximum...
#
def TotalRoleProbabilities(outputbase, organismid, role_probability_file):
    '''Given the probability that each gene has each role, estimate the probability that
    the entire ORGANISM has that role.

    To avoid exploding the probabilities with noise, I just take the maximum probability
    of any query gene having a function and use that as the probability that the function
    exists in the cell.

    Returns a file with three columns: each role, its probability, and the estimated set of genes
    that perform that role. A gene is assigned to a role if it is within DILUTION_PERCENT
    of the maximum probability. DILUTION_PERCENT is defined in the python_globals.py file.

    '''
    total_role_probability_file = os.path.join(outputbase, organismid, "%s.cellroleprob" %(organismid))
    try:
        fid = open(total_role_probability_file, "r")
        fid.close()
        sys.stderr.write("Whole-cell role probability file %s already exists\n" %(total_role_probability_file))
        return total_role_probability_file
    except IOError:
        pass

    sys.stderr.write("Generating whole-cell role probability file...")
    roleToTotalProb = {}
    for line in open(role_probability_file, "r"):
        spl = line.strip("\r\n").split("\t")
        # Compute maximum probability among all query genes for each role.
        if spl[1] in roleToTotalProb:
            if float(spl[2]) > roleToTotalProb[spl[1]]:
                roleToTotalProb[spl[1]] = float(spl[2])
        else:
            roleToTotalProb[spl[1]] = float(spl[2])

    # Get the genes within DILUTION_PERCENT percent of the maximum
    # probability and assert those (note - DILUTION_PERCENT is defined in the global parameters file)
    # This is a dictionary from role to a list of genes
    roleToGeneList = {}
    for line in open(role_probability_file, "r"):
        spl = line.strip("\r\n").split("\t")
        if spl[1] not in roleToTotalProb:
            sys.stderr.write("ERROR: Role %s not placed properly in roleToTotalProb dictionary?\n" %(spl[1]))
            exit(1)
        if float(spl[2]) >= float(DILUTION_PERCENT)/100.0 * roleToTotalProb[spl[1]]:
            if spl[1] in roleToGeneList:
                roleToGeneList[spl[1]].append(spl[0])
            else:
                roleToGeneList[spl[1]] = [ spl[0] ]
            
    fid = open(total_role_probability_file, "w")
    for role in roleToTotalProb:
        fid.write("%s\t%s\t%s\n" %(role, roleToTotalProb[role], " or ".join(roleToGeneList[role])))
    fid.close()
    sys.stderr.write("done\n")

    return total_role_probability_file

def ComplexProbabilities(outputbase, organismid, total_role_probability_file, folder):
    '''Compute the probability of each complex from the probability of each role.

    The complex probability is computed as the minimum probability of roles within that complex.
    
    The output file to this has four columns
    Complex   |   Probability   | Type   |  Roles_not_in_organism  |  Roles_not_in_subsystems
    
    Type: 

    CPLX_FULL (all roles found and utilized)
    CPLX_PARTIAL (only some roles found - only those roles that were found were utilized; does not distinguish between not there and no reps for those not found)
    CPLX_NOTTHERE (Probability of 0 because the genes aren't there for any of the subunits)
    CPLX_NOREPS (Probability 0f 0 because there are no representative genes in the subsystems)

    '''
    # 0 - check if the complex probability file already exists
    complex_probability_file = os.path.join(outputbase, organismid, "%s.complexprob" %(organismid))

    try:
        fid = open(complex_probability_file, "r")
        fid.close()
        sys.stderr.write("Complex probability file %s already exists\n" %(complex_probability_file))
        return complex_probability_file
    except IOError:
        pass

    sys.stderr.write("Computing complex probabilities...")
    # 1 - Read required data:
    # complexes --> roles 
    complexesToRequiredRoles = readComplexRoles(folder)
    # subsystem roles
    # (used to distinguish between NOTTHERE and NOREPS)
    otu_fidsToRoles, otu_rolesToFids  = readFilteredOtuRoles(folder)
    allroles = set()
    for fid in otu_fidsToRoles:
        for role in otu_fidsToRoles[fid]:
            allroles.add(role)

    # 2: Read the total role --> probability file
    rolesToProbabilities = {}
    rolesToGeneList = {}
    for line in open(total_role_probability_file, "r"):
        spl = line.strip("\r\n").split("\t")
        rolesToProbabilities[spl[0]] = float(spl[1])
        rolesToGeneList[spl[0]] = spl[2]

    # 3: Iterate over complexes and compute complex probabilities from role probabilities.
    # Separate out cases where no genes seem to exist in the organism for the reaction from cases
    # where there is a database deficiency.
    fid = open(complex_probability_file, "w")
    for cplx in complexesToRequiredRoles:
        allCplxRoles = complexesToRequiredRoles[cplx]
        availRoles = [] # Roles that may have representatives in the query organism
        unavailRoles = [] # Roles that have representatives but that are not apparently in the query organism
        noexistRoles = [] # Roles with no representatives in the subsystems
        for role in complexesToRequiredRoles[cplx]:
            if role not in allroles:
                noexistRoles.append(role)
            elif role not in rolesToProbabilities:
                unavailRoles.append(role)
            else:
                availRoles.append(role)
        TYPE = ""
        GPR = ""
        if len(noexistRoles) == len(allCplxRoles):
            TYPE = "CPLX_NOREPS"
            fid.write("%s\t%1.4f\t%s\t%s\t%s\t%s\n" %(cplx, 0.0, TYPE, SEPARATOR.join(unavailRoles), SEPARATOR.join(noexistRoles), GPR))
            continue
        if len(unavailRoles) == len(allCplxRoles):
            TYPE = "CPLX_NOTTHERE"
            fid.write("%s\t%1.4f\t%s\t%s\t%s\t%s\n" %(cplx, 0.0, TYPE, SEPARATOR.join(unavailRoles), SEPARATOR.join(noexistRoles), GPR))
            continue
        # Some had no representatives and the rest were not found in the cell
        if len(unavailRoles) + len(noexistRoles) == len(allCplxRoles):
            TYPE = "CPLX_NOREPS_AND_NOTTHERE"
            fid.write("%s\t%1.4f\t%s\t%s\t%s\t%s\n" %(cplx, 0.0, TYPE, SEPARATOR.join(unavailRoles), SEPARATOR.join(noexistRoles), GPR))
            continue
        # Otherwise at least one of them is available
        if len(availRoles) == len(allCplxRoles):
            TYPE = "CPLX_FULL"
        elif len(availRoles) < len(allCplxRoles):
            TYPE = "CPLX_PARTIAL_%d_of_%d" %(len(availRoles), len(allCplxRoles))
        GPR = " and ".join("(" + s + ")" for s in [ rolesToGeneList[f] for f in availRoles ] )
        if GPR != "==":
            GPR = "(" + GPR + ")"
        # Find the minimum probability of the different available roles (ignoring ones that are apparently missing)
        # and call that the complex probability
        minp = 1000
        for role in availRoles:
            if rolesToProbabilities[role] < minp:
                minp = rolesToProbabilities[role]
        fid.write("%s\t%1.4f\t%s\t%s\t%s\t%s\n" %(cplx, minp, TYPE, SEPARATOR.join(unavailRoles), SEPARATOR.join(noexistRoles), GPR))

    fid.close()
    sys.stderr.write("done\n")
    return complex_probability_file

def ReactionProbabilities(outputbase, organismid, complex_probability_file, folder):
    '''From the probability of complexes estimate the probability of reactions.

    The reaction probability is computed as the maximum probability of complexes that perform
    that reaction.

    The output to this function is a file containing the following columns:
    Reaction   |  Probability  |  RxnType  |  ComplexInfo

    If the reaction has no complexes it won't even be in this file becuase of the way
    I set up the call... I could probably change this so that I get a list of ALL reactions
    and make it easier to catch issues with reaction --> complex links in the database.
    Some of the infrastructure is already there (with the TYPE).

    ComplexInfo is information about the complex IDs, their probabilities, and their TYPE
    (see ComplexProbabilities)

    '''

    reaction_probability_file = os.path.join(outputbase, organismid, "%s.rxnprobs" %(organismid))
    try:
        fid = open(reaction_probability_file, "r")
        fid.close()
        sys.stderr.write("Reaction probability file %s already exists\n" %(reaction_probability_file))
        return reaction_probability_file
    except IOError:
        pass

    sys.stderr.write("Computing reaction probabilities...")
    # Cplx --> {Probability, type, GPR}
    cplxToTuple = {}
    for line in open(complex_probability_file, "r"):
        spl = line.strip("\r\n").split("\t")
        cplxToTuple[spl[0]] = ( float(spl[1]), spl[2], spl[5] )
    
    # Take the MAXIMUM probability of complexes
    rxnsToComplexes = readReactionComplex(folder)

    fid = open(reaction_probability_file, "w")
    for rxn in rxnsToComplexes:
        TYPE = "NOCOMPLEXES"
        rxnComplexes = rxnsToComplexes[rxn]
        complexinfo = ""
        maxp = 0
        GPR = ""
        for cplx in rxnComplexes:
            if cplx in cplxToTuple:
                # Complex1 (P1; TYPE1) ///Complex2 (P2; TYPE2) ...
                complexinfo = "%s%s%s (%1.4f; %s) " %(complexinfo, SEPARATOR, cplx, cplxToTuple[cplx][0], cplxToTuple[cplx][1])
                TYPE = "HASCOMPLEXES"
                if cplxToTuple[cplx][0] > maxp:
                    maxp = cplxToTuple[cplx][0]
                if GPR == "":
                    GPR = cplxToTuple[cplx][2]
                elif cplxToTuple[cplx][2] != "":
                    GPR = " or ".join( [ GPR, cplxToTuple[cplx][2] ] )
        fid.write("%s\t%1.4f\t%s\t%s\t%s\n" %(rxn, maxp, TYPE, complexinfo, GPR))
    fid.close()

    # (end of function)
    sys.stderr.write("done\n")
    return reaction_probability_file

def MakeProbabilisticJsonFile(annotation_file, blast_result_file, roleset_probability_file, outfile, folder, genome_id, probanno_id):
    '''Create a "probabilistic annotation" object file from a genome object file. The probabilistic annotation
    file adds fields for the probability of each role being linked to each gene.'''

    sys.stderr.write("Making probabilistic JSON file %s...\n" %(outfile))
    targetToRoles, rolesToTargets = readFilteredOtuRoles(folder)
    targetToRoleSet = {}
    for target in targetToRoles:
        stri = SEPARATOR.join(sorted(targetToRoles[target]))
        targetToRoleSet[target] = stri
    queryToTargetProbs = parseBlastOutput(blast_result_file)
    queryToRolesetProbs = readRolesetProbabilityFile(roleset_probability_file)

    # Read the existing annotation_file.
    resp = json.load(open(annotation_file, "r"))
    if "features" not in resp:
        sys.stderr.write("""ERROR: The input JSON file %s has no features - did you forget to run annotate_genome?\n""" %(annotation_file))
        return None

    # For each query ID:
    # 1. Identify their rolestring probabilities (these are the first and second elements of the tuple)
    # 2. Iterate over the target genes and identify those with each function (a list of these and their blast scores is
    #    the third element of the tuple) - should be able to set it up to only iterate once over the list.
    # 3. Add that tuple to the JSON file with the key "alternativeFunctions"

    # resp["features"] is a list of dictionaries. We want to make our data structure and then add that to the dictionary.
    # I use the ii in range so I can edit the elements without changes being lost.

    #FIXME - these should become inputs to this function.
    probanno_json = {}
    probanno_json["id"] = probanno_id
    probanno_json["genome_id"] = genome_id
    probanno_json["featureAlternativeFunctions"] = []

    for ii in range(len(resp["features"])):
        feature = resp["features"][ii]
        if "id" not in resp:
            sys.stderr.write("No gene ID found in input JSON file (this should never happen)\n")
            return None
        queryid = feature["id"]
        # This can happen if I couldn't find hits from that gene to anything in the database. In this case, I'll just skip it.
        # Or should I make an empty object? I should ask Chris.
        if queryid not in queryToRolesetProbs or queryid not in queryToTargetProbs:
#            sys.stderr.write("No match for ID %s\n" %(queryid))
            continue
        # Get a list of (target ID, BLAST score) pairs for each possible role set from the query's homologs
        # At the moment we dont' use this...
        # I'm keeping the code around in case we decide we want the functionality.
        query_rolesetToTargetScores = {}
        for targetprob in queryToTargetProbs[queryid]:
            try:
                targetroleset = targetToRoleSet[targetprob[0]]
            except KeyError:
                sys.stderr.write("WARNING: Internal data inconsistency - BLAST target %s has no associated roleset\n" %(targetprob[0]))
                continue
            if targetroleset in query_rolesetToTargetScores:
                query_rolesetToTargetScores[targetroleset].append(targetprob)
            else:
                query_rolesetToTargetScores[targetroleset] = [ targetprob ]

        # For each possible roleset, obtain its probability, and the list of (target, BLAST score) pairs
        featureAlternativeFunctions = {}
        alternative_functions = []
        for rolesetprob in queryToRolesetProbs[queryid]:
            roleset = rolesetprob[0]
            rolep = rolesetprob[1]
            if roleset not in query_rolesetToTargetScores:
                sys.stderr.write("INTERNAL ERROR: The rolesets for the query genes were not transferred from targets correctly - query roleset %s did not exist in the target homolog list\n" %(roleset))
                exit(2)
            rlist = [ roleset, rolep ]
            alternative_functions.append(rlist)
        featureAlternativeFunctions["alternativeFunctions"] = alternative_functions
        featureAlternativeFunctions["id"] = queryid
        probanno_json["featureAlternativeFunctions"].append(featureAlternativeFunctions)

    json.dump(probanno_json, open(outfile, "w"), indent=4)
    return