#BEGIN_HEADER
import sys, tempfile, shutil
import os, traceback
from random import randint
from biokbase.probabilistic_annotation.DataExtractor import *
from biokbase.probabilistic_annotation.DataParser import *
from biokbase.probabilistic_annotation.Shock import Client as ShockClient
from biokbase.workspaceService.client import *
from biokbase.fbaModelServices.Client import *
from biokbase.cdmi.client import CDMI_EntityAPI

# Current version number of ProbAnno object
ProbAnnoVersion = 1

# Current version number of RxnProbs object
RxnProbsVersion = 1

# Exception thrown when static database files are not ready
class NotReadyError(Exception):
    pass

# Exception thrown when static database file is missing from Shock.
class MissingFileError(Exception):
    pass

# Exception thrown when no features are found in Genome object
class NoFeaturesError(Exception):
    pass

# Exception thrown when blast command failed
class BlastError(Exception):
    pass

# Exception thrown when there are no gene IDs in Genome object
class NoGeneIdsError(Exception):
    pass

# Exception thrown when role not found in roleToTotalProb dictionary
class RoleNotFoundEror(Exception):
    pass

# Exception thrown when object version is not valid
class WrongVersionError(Exception):
    pass
#END_HEADER

'''

Module Name:
ProbabilisticAnnotation

Module Description:
The purpose of the Probabilistic Annotation service is to provide users with
alternative annotations for genes, each attached to a likelihood score, and to
translate these likelihood scores into likelihood scores for the existence of
reactions in metabolic models.  With the Probabilistic Annotation service:

- Users can quickly assess the quality of an annotation.

- Reaction likelihood computations allow users to estimate the quality of
  metabolic networks generated using the automated reconstruction tools in
  other services.

- Combining reaction likelihoods with gapfilling both directly incorporates
  available genetic evidence into the gapfilling process and provides putative
  gene annotations automatically, reducing the effort needed to search for
  evidence for gapfilled reactions.

'''
class ProbabilisticAnnotation:

    #BEGIN_CLASS_HEADER
    def _checkInputArguments(self, input, requiredArgs, defaultArgDict):
        ''' Check that required input arguments are present and set defaults for non-required arguments.
        
        input is a dictionary from option to value
        requiredArgs is a list of required options in the input dict
        defaultArgDict is a dictionary of default options.

        If a key in defaultArgDict is not found in the input it is added with the
        specified default value.
        '''
        if requiredArgs is not None:
            for arg in requiredArgs:
                if arg not in input:
                    raise IOError("Required argument %s not found" %(arg) )
        if defaultArgDict is not None:
            for arg in defaultArgDict:
                if arg in input:
                    continue
                else:
                    input[arg] = defaultArgDict[arg]

        return input

    def runAnnotate(self, job):
        '''Run an annotate job.

        job is a workspace job object.
        '''

        # Create a workspace client.
        wsClient = workspaceService(self.config["workspace_url"])

        # The input parameters to annotate() were stored in the jobdata for the job.
        input = job["jobdata"]

        status = None

        try:        
            # Get the Genome object from the specified workspace.
            getObjectParams = { "type": "Genome", "id": input["genome"], "workspace": input["genome_workspace"], "auth": input["auth"] }
            genomeObject = wsClient.get_object(getObjectParams)
            
            # Create a temporary directory for storing blast input and output files.
            workFolder = os.path.join(self.config["work_folder_path"], "jobs", job["id"])
            
            # Convert Genome object to fasta file.
            fastaFile = self._genomeToFasta(input, genomeObject, workFolder)
            
            # Run blast using the fasta file.
            blastResultFile = self._runBlast(input, fastaFile, workFolder)
            
            # Calculate roleset probabilities.
            rolestringTuples = self._rolesetProbabilitiesMarble(input, input["genome"], blastResultFile, workFolder)
            
            # Build ProbAnno object and store in the specified workspace.
            output = self._buildProbAnnoObject(input, genomeObject, blastResultFile, rolestringTuples, workFolder, wsClient)

            # Mark the job as done.
            status = "done"

            # Remove the temporary directory only if our job succeeds. If it does that means there were no errors so we don't need it.
            if self.config["debug"] == False:
                shutil.rmtree(workFolder)
        except ServerError:
            # We failed to get the object out of the workspace
            # This doesn't quite work the way I want it to...
            status = "error"
            sys.stderr.write("ERROR - ServerError (most likely we failed to get the object out of the workspace\n")
        except:
            status = "error"
            traceback.print_exc(file=sys.stderr)
        finally:
            if self.config["job_queue"] == "scheduler":
                setStatusParams = { "jobid": job["id"], "status": status, "auth": job["auth"] }
                wsClient.set_job_status(setStatusParams)           

        return
        
    def _genomeToFasta(self, input, genomeObject, workFolder):
        '''Convert a Genome object into an amino-acid FASTA file (for BLAST purposes).

        input is a dictionary of input options
        genomeObject is a genome object
        workFolder is a directory in which to temporarily dump the fasta file.
        '''
        
        # Make sure the Genome object has features.
        if "features" not in genomeObject["data"]:
            raise NoFeaturesError("The input Genome object %s/%s has no features. Did you forget to run annotate_genome?\n" %(input["genome_workspace"], input["genome"]))
    
        # Open the fasta file.
        fastaFile = os.path.join(workFolder, "%s.faa" %(input["genome"]))
        fid = open(fastaFile, "w")
        
        # Run the list of features to build the fasta file.
        features = genomeObject["data"]["features"]
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
            fid.write(">%s %s\n%s\n" %(myid, function, seq))
        
        fid.close()    
        return fastaFile
        
    def _runBlast(self, input, queryFile, workFolder):
        '''A simplistic wrapper to BLAST the query proteins against the subsystem proteins.

        input is a dictionary of input options.
        queryFile is the name of a BLASTP database.
        workFolder is a directory in which to save the results. 

        Returns the name of the output file from BLAST in tab-delimited format (outfmt 6).
        '''
        
        blastResultFile = os.path.join(workFolder, "%s.blastout" %(input["genome"]))
        cmd = "blastp -query \"%s\" -db %s -outfmt 6 -evalue 1E-5 -num_threads 1 -out \"%s\"" %(queryFile, os.path.join(self.config["data_folder_path"], DatabaseFiles["subsystem_otu_fasta_file"]), blastResultFile)
        sys.stderr.write("Started BLAST with command: %s\n" %(cmd))
        status = os.system(cmd)
        sys.stderr.write("Ended BLAST with command: %s\n" %(cmd))
        if os.WIFEXITED(status):
            if os.WEXITSTATUS(status) != 0:
                raise BlastError("'%s' failed with status %d\n" %(cmd, os.WEXITSTATUS(status)))
        if os.WIFSIGNALED(status):
            raise BlastError("'%s' ended by signal %d\n" %(cmd, os.WTERMSIG(status)))
        return blastResultFile
    
    def _rolesetProbabilitiesMarble(self, input, genome, blastResultFile, workFolder):
        '''Calculate the probabilities of rolesets (i.e. each possible combination of roles implied by the functions of the proteins in subsystems) from the BLAST results.

        input is a dictionary of input options
        genome is a genome object
        blastResultFile is the name of a file containing tab-delimited BLAST results for that genome against some blast database
        workFolder is a directory in which to save the results.
    
        Returns a file with three columns(query, roleset_string, probability)  
        roleset_string = "\\\" separating all roles of a protein with a single function (order does not matter)
        '''
    
        sys.stderr.write("Performing marble-picking on rolesets...")
    
        # Read in the target roles (this function returns the roles as lists!)
        targetIdToRole, targetRoleToId = readFilteredOtuRoles(self.config)
    
        # Convert the lists of roles into "rolestrings" (sort the list so that order doesn't matter)
        # in order to deal with the case where some of the hits are multi-functional and others only have
        # a single function...
        targetIdToRoleString = {}
        for target in targetIdToRole:
            stri = self.config["separator"].join(sorted(targetIdToRole[target]))
            targetIdToRoleString[target] = stri
    
        # Query --> [ (target1, score 1), (target 2, score 2), ... ]
        idToTargetList = parseBlastOutput(blastResultFile)
    
        # This is a holder for all of our results
        # It is a dictionary query -> [ (roleset1, probability_1), (roleset2, probability_2), ...]
        rolestringTuples = {}
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
            denom = float(self.config["pseudo_count"]) * maxscore
            for stri in rolestringToScore:
                denom += rolestringToScore[stri]
    
            # Now the numerators, which are different for every rolestring
            for stri in rolestringToScore:
                p = rolestringToScore[stri]/denom
                if query in rolestringTuples:
                    rolestringTuples[query].append( (stri, p) )
                else:
                    rolestringTuples[query] = [ (stri, p) ]
    
        # Save the generated data when debug is turned on.
        if self.config["debug"]:
            rolesetProbabilityFile = os.path.join(workFolder, "%s.rolesetprobs" %(genome))
            fid = open(rolesetProbabilityFile, "w")
            for query in rolestringTuples:
                for tup in rolestringTuples[query]:
                    fid.write("%s\t%s\t%1.4f\n" %(query, tup[0], tup[1]))
            fid.close()
            
        sys.stderr.write("done\n")
        return rolestringTuples
            
    def _buildProbAnnoObject(self, input, genomeObject, blastResultFile, queryToRolesetProbs, workFolder, wsClient):
        '''Create a "probabilistic annotation" object file from a Genome object file. 

        input: A list of input options
        genomeObject: A genome object
        blastResultFile: The name of a file containing BLAST results in tab-delimited format
        queryToRolesetProbs: A dictionary querygene-> [ (roleset, probability), ... ]
        workFolder: A directory in which to save the results
        wsClient: A workspace client object

        The probabilistic annotation object adds fields for the probability of each role being linked to each gene.'''
    
        sys.stderr.write("Building ProbAnno object for genome %s..." %(input["genome"]))
        targetToRoles, rolesToTargets = readFilteredOtuRoles(self.config)
        targetToRoleSet = {}
        for target in targetToRoles:
            stri = self.config["separator"].join(sorted(targetToRoles[target]))
            targetToRoleSet[target] = stri
        # This is a dictionary from query ID to (target, -log E-value) pairs.
        # We just use it to identify whether or not we actually hit anything in the db
        # when searching for the query gene.
        queryToTargetEvals = parseBlastOutput(blastResultFile)
        
        # For each query ID:
        # 1. Identify their rolestring probabilities (these are the first and second elements of the tuple)
        # 2. Iterate over the target genes and identify those with each function (a list of these and their blast scores is
        #    the third element of the tuple) - should be able to set it up to only iterate once over the list.
        # 3. Add that tuple to the JSON file with the key "alternativeFunctions"
    
        # The Genome object data ["features"] is a list of dictionaries. We want to make our data structure and 
        # then add that to the dictionary.  I use the ii in range so I can edit the elements without changes being lost.
    
        objectData = {}
        objectData["id"] = input["probanno"]
        objectData["genome"] = input["genome"]
        objectData["genome_workspace"] = input["genome_workspace"];
        objectData["roleset_probabilities"] = queryToRolesetProbs;
        objectData["skipped_features"] = []
        
        for ii in range(len(genomeObject["data"]["features"])):
            feature = genomeObject["data"]["features"][ii]
            if "id" not in genomeObject["data"]:
                raise NoGeneIdsError("No gene IDs found in input Genome object %s/%s (this should never happen)" %(input["genome_workspace"], input["genome"]))
            queryid = feature["id"]
    
            # This can happen if I couldn't find hits from that gene to anything in the database. In this case, I'll just skip it.
            # TODO Or should I make an empty object? I should ask Chris.
            if queryid not in queryToRolesetProbs or queryid not in queryToTargetEvals:
                objectData["skipped_features"].append(queryid)
                
        # Store the ProbAnno object in the specified workspace.
        objectMetadata = { "version": ProbAnnoVersion, "num_rolesets": len(objectData["roleset_probabilities"]),
                           "num_skipped_features": len(objectData["skipped_features"]) }
        saveObjectParams = { "type": "ProbAnno", "id": input["probanno"], "workspace": input["probanno_workspace"],
                             "auth": input["auth"], "data": objectData, "metadata": objectMetadata, "command": "pa-annotate" }
        metadata = wsClient.save_object(saveObjectParams)
        
        sys.stderr.write("done\n")
        return metadata
    
    def _rolesetProbabilitiesToRoleProbabilities(self, input, genome, queryToTuplist, workFolder):
        '''Compute probability of each role from the rolesets for each query protein.

        input: A list of input options
        genome: A Genome object
        queryToTuplist: A dictionary querygene -> [ (roleset, probability), ... ]
        workFolder: A directory in which to save the results

        At the moment the strategy is to take any set of rolestrings containing the same roles
        and add their probabilities.
        So if we have hits to both a bifunctional enzyme with R1 and R2, and
        hits to a monofunctional enzyme with only R1, R1 ends up with a greater
        probability than R2.
    
        I had tried to normalize to the previous sum but I need to be more careful than that
        (I'll put it on my TODO list) because if you have e.g.
        one hit to R1R2 and one hit to R3 then the probability of R1 and R2 will be unfairly
        brought down due to the normalization scheme...
    
        Returns a list of (querygene, role, probability) tuples'''
    
        if input["verbose"]:
            sys.stderr.write("%s: Started computing role probabilities from roleset probabilities\n" %(genome))
    
        roleProbs = []    
        for query in queryToTuplist:
            # This section actually does the conversion of probabilities.
            queryRolesToProbs = {}
            for tup in queryToTuplist[query]:
                rolelist = tup[0].split(self.config["separator"])
                # Add up all the instances of each particular role on the list.
                for role in rolelist:
                    if role in queryRolesToProbs:
                        queryRolesToProbs[role] += tup[1]
                    else:
                        queryRolesToProbs[role] = tup[1]
    
            # Add them to the array.
            for role in queryRolesToProbs:
                roleProbs.append( (query, role, queryRolesToProbs[role]) )
    
        # Save the generated data when debug is turned on.
        if self.config["debug"]:
            role_probability_file = os.path.join(workFolder, "%s.roleprobs" %(genome))
            fid = open(role_probability_file, "w")
            for tuple in roleProbs:
                fid.write("%s\t%s\t%s\n" %(tuple[0], tuple[1], tuple[2]))
            fid.close()

        if input["verbose"]:    
            sys.stderr.write("%s: Finished computing role probabilities from roleset probabilities\n" %(genome))
            
        return roleProbs
    
    # For now to get the probability I just assign this as the MAXIMUM for each role
    # to avoid diluting out by noise.
    #
    # The gene assignments are all genes within DILUTION_PERCENT of the maximum...
    #
    def _totalRoleProbabilities(self, input, genome, roleProbs, workFolder):
        '''Given the probability that each gene has each role, estimate the probability that the entire ORGANISM has that role.

        input: A list of input options
        genome: A Genome object
        roleProbs: A list of (gene, role, probability) tuples
        
        
        To avoid exploding the probabilities with noise, I just take the maximum probability
        of any query gene having a function and use that as the probability that the function
        exists in the cell.
    
        Returns a file with three columns: each role, its probability, and the estimated set of genes
        that perform that role. A gene is assigned to a role if it is within DILUTION_PERCENT
        of the maximum probability. DILUTION_PERCENT can be adjusted in the config file.
        '''
    
        if input["verbose"]:
            sys.stderr.write("%s: Started generating whole-cell role probability file\n" %(genome))        
    
        # Compute maximum probability among all query genes for each role.
        # This is assumed to be the probability of that role occuring in the organism as a whole.
        roleToTotalProb = {}
        for tuple in roleProbs:
            if tuple[1] in roleToTotalProb:
                if float(tuple[2]) > roleToTotalProb[tuple[1]]:
                    roleToTotalProb[tuple[1]] = float(tuple[2])
            else:
                roleToTotalProb[tuple[1]] = float(tuple[2])
    
        # Get the genes within DILUTION_PERCENT percent of the maximum
        # probability and assert that these are the most likely genes responsible for that role.
        # (note - DILUTION_PERCENT is defined in the config file)
        # This produces a dictionary from role to a list of genes
        roleToGeneList = {}
        for tuple in roleProbs:
            if tuple[1] not in roleToTotalProb:
                raise RoleNotFoundError("Role %s not placed properly in roleToTotalProb dictionary?" %(tuple[1]))
            if float(tuple[2]) >= float(self.config["dilution_percent"])/100.0 * roleToTotalProb[tuple[1]]:
                if tuple[1] in roleToGeneList:
                    roleToGeneList[tuple[1]].append(tuple[0])
                else:
                    roleToGeneList[tuple[1]] = [ tuple[0] ]
        
        # Build the array of total role probabilities.     
        totalRoleProbs = []
        for role in roleToTotalProb:
            totalRoleProbs.append( (role, roleToTotalProb[role], " or ".join(roleToGeneList[role])) )   
    
        # Save the generated data when debug is turned on.
        if self.config["debug"]:
            total_role_probability_file = os.path.join(workFolder, "%s.cellroleprob" %(genome))
            fid = open(total_role_probability_file, "w")
            for tuple in totalRoleProbs:
                fid.write("%s\t%s\t%s\n" %(tuple[0], tuple[1], tuple[2]))
            fid.close()
        
        if input["verbose"]:    
            sys.stderr.write("%s: Finished generating whole-cell role probability file\n" %(genome))
            
        return totalRoleProbs
    
    def _complexProbabilities(self, input, genome, totalRoleProbs, workFolder, complexesToRequiredRoles = None):
        '''Compute the probability of each complex from the probability of each role.

        input is a dictionary of input parameters
        genome is a genome object
        totalRoleProbs is a list of (gene, role, probability) tuples
        workFolder is a folder in which to save the results
        complexesToRequiredRoles is a dictionary from complex to the roles involved in forming that complex. If it is None
                                    we read it from the CDMI files we downloaded, otherwise we use the provided dictionary.
                                    This is included for template model support in the future.
    
        The complex probability is computed as the minimum probability of roles within that complex (ignoring roles not represented in the subsystems).
        
        The output to this is a list of tuples with the following five fields (a file is printed with the same format if "debug" is specified as an input):
        Complex   |   Probability   | Type   |  Roles_not_in_organism  |  Roles_not_in_subsystems
        
        Type is a string with one of the following values: 
    
        CPLX_FULL (all roles found and utilized in the complex)
        CPLX_PARTIAL (only some roles found - only those roles that were found were utilized; does not distinguish between not there and no reps for those not found)
        CPLX_NOTTHERE (Probability of 0 because the genes aren't there for any of the subunits)
        CPLX_NOREPS (Probability of 0 because there are no representative genes in the subsystems for any of the subunits)
        '''
    
        if input["verbose"]:
            sys.stderr.write("%s: Started computing complex probabilities\n" %(genome))
    
        # Get the mapping from complexes to roles if it isn't already provided.
        if complexesToRequiredRoles is None:
            complexesToRequiredRoles = readComplexRoles(self.config)
        
        # Get the subsystem roles (used to distinguish between NOTTHERE and NOREPS).
        otu_fidsToRoles, otu_rolesToFids  = readFilteredOtuRoles(self.config)
        allroles = set()
        for fid in otu_fidsToRoles:
            for role in otu_fidsToRoles[fid]:
                allroles.add(role)
    
        # 2: Read the total role --> probability file
        rolesToProbabilities = {}
        rolesToGeneList = {}
        for tuple in totalRoleProbs:
            rolesToProbabilities[tuple[0]] = float(tuple[1]) # can skip the float()?
            rolesToGeneList[tuple[0]] = tuple[2]
    
        # Iterate over complexes and compute complex probabilities from role probabilities.
        # Separate out cases where no genes seem to exist in the organism for the reaction from cases
        # where there is a database deficiency.
        SEPARATOR = self.config["separator"]
        complexProbs = []
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
                complexProbs.append( (cplx, 0.0, TYPE, self.config["separator"].join(unavailRoles), self.config["separator"].join(noexistRoles), GPR) )
                continue
            if len(unavailRoles) == len(allCplxRoles):
                TYPE = "CPLX_NOTTHERE"
                complexProbs.append( (cplx, 0.0, TYPE, self.config["separator"].join(unavailRoles), self.config["separator"].join(noexistRoles), GPR) )
                continue
            # Some had no representatives and the rest were not found in the cell
            if len(unavailRoles) + len(noexistRoles) == len(allCplxRoles):
                TYPE = "CPLX_NOREPS_AND_NOTTHERE"
                complexProbs.append( (cplx, 0.0, TYPE, self.config["separator"].join(unavailRoles), self.config["separator"].join(noexistRoles), GPR) )
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
            complexProbs.append( (cplx, minp, TYPE, self.config["separator"].join(unavailRoles), self.config["separator"].join(noexistRoles), GPR) )
    
        if self.config["debug"]:
            complex_probability_file = os.path.join(workFolder, "%s.complexprob" %(genome))
            fid = open(complex_probability_file, "w")
            for tuple in complexProbs:
                fid.write("%s\t%1.4f\t%s\t%s\t%s\t%s\n" %(tuple[0], tuple[1], tuple[2], tuple[3], tuple[4], tuple[5]))
            fid.close()
        
        if input["verbose"]:
            sys.stderr.write("%s: Finished computing complex probabilities\n" %(genome))
        return complexProbs
    
    def _reactionProbabilities(self, input, genome, complexProbs, workFolder, rxnsToComplexes = None):
        '''From the probability of complexes estimate the probability of reactions.
    
        The reaction probability is computed as the maximum probability of complexes that perform
        that reaction.
    
        The output to this function is a list of tuples containing the following elements:
        Reaction   |  Probability  |  RxnType  |  ComplexInfo | GPR

        If the "debug" flag is set we also produce a file with these columns in the workFolder.
    
        If the reaction has no complexes it won't even be in this file becuase of the way
        I set up the call... I could probably change this so that I get a list of ALL reactions
        and make it easier to catch issues with reaction --> complex links in the database.
        Some of the infrastructure is already there (with the TYPE).
    
        ComplexProbs is information about the complex IDs, their probabilities, and their TYPE
        (see ComplexProbabilities)

        rxnsToComplexes - this is None unless it is provided by Chris's function. Otherwise it is a
        dictionary from reaction to a list of catalyzing complexes.
        '''
    
        if input["verbose"]:
            sys.stderr.write("%s: Started computing reaction probabilities\n" %(genome))
        
        # Cplx --> {Probability, type, GPR}
        cplxToTuple = {}
        for tuple in complexProbs:
            cplxToTuple[tuple[0]] = ( tuple[1], tuple[2], tuple[5] )
        
        if rxnsToComplexes is None:
            rxnsToComplexes = readReactionComplex(self.config)

        # Take the MAXIMUM probability of complexes catalyzing a particular reaction
        # and call that the complex probability.
        reactionProbs = []
        for rxn in rxnsToComplexes:
            TYPE = "NOCOMPLEXES"
            rxnComplexes = rxnsToComplexes[rxn]
            complexinfo = ""
            maxp = 0
            GPR = ""
            for cplx in rxnComplexes:
                if cplx in cplxToTuple:
                    # Complex1 (P1; TYPE1) ///Complex2 (P2; TYPE2) ...
                    complexinfo = "%s%s%s (%1.4f; %s) " %(complexinfo, self.config["separator"], cplx, cplxToTuple[cplx][0], cplxToTuple[cplx][1])
                    TYPE = "HASCOMPLEXES"
                    if cplxToTuple[cplx][0] > maxp:
                        maxp = cplxToTuple[cplx][0]
                        pass
                    pass
            # Iterate separately to get a GPR. We want to apply a cutoff here too to avoid a complex with 80% probability being linked by OR to another with a 5% probability, for example...
            # For now I've implemented using the same cutoff as we used for which genes go with a role.
            for cplx in rxnComplexes:
                if cplx in cplxToTuple:
                    if cplxToTuple[cplx][0] < maxp * float(self.config["dilution_percent"])/100.0:
                        continue
                    if GPR == "":
                        GPR = cplxToTuple[cplx][2]
                    elif cplxToTuple[cplx][2] != "":
                        GPR = " or ".join( [ GPR, cplxToTuple[cplx][2] ] )
            # List so that we can modify the reaction IDs if needed to translate to ModelSEED IDs
            reactionProbs.append( [rxn, maxp, TYPE, complexinfo, GPR] )
    
        if self.config["debug"]:
            reaction_probability_file = os.path.join(workFolder, "%s.rxnprobs" %(genome))
            fid = open(reaction_probability_file, "w")
            for tuple in reactionProbs:
                fid.write("%s\t%1.4f\t%s\t%s\t%s\n" %(tuple[0], tuple[1], tuple[2], tuple[3], tuple[4]))
            fid.close()
    
        if input["verbose"]:
            sys.stderr.write("%s: Finished computing reaction probabilities\n" %(genome))
        return reactionProbs
    
    def _metaboliteWeights(input, model):
        '''Given a model object, computes an S-matrix.
     
        model: Model object

        This function returns three things:
        - A sparse matrix object (coo_matrix) from scipy with indexes matching the lists
        - A dictionary from metabolite UUIDs to their index in the matrix
        - A dictionary from reaction UUIDs to their index in the matrix
     
        The lists should have the same order as the input model.
     
        If absval = True, returns the absolute value of the S-matrix (absolute value of every
        term in the S matrix) rather than S itself.
         
        TODO - put in biomass equation too.
        '''
     
        print(input)
        metIdToIdx = {}
        rxnIdToIdx = {}
     
        idx = 0
        for compound in model["modelcompounds"]:
            metIdToIdx[compound["uuid"]] = idx
            idx += 1
         
        i = []
        j = []
        data = []
        costs = []
        idx = 0
        numZeroReagents = 0
        for reaction in model["modelreactions"]:
            if "modelReactionReagents" not in reaction:
                numZeroReagents += 1
                continue
            for reagent in reaction["modelReactionReagents"]:
                met_uuid = reagent["modelcompound_uuid"]
                coefficient = reagent["coefficient"]
                met_idx = metIdToIdx[met_uuid]
                i.append(met_idx)
                j.append(idx)
                if input["absval"]:
                    coefficient = abs(coefficient)
                data.append(coefficient)
            costs.append(1.0 - reaction["probability"])
            rxnIdToIdx[reaction["uuid"]] = idx
            idx += 1
     
        S_matrix = sparse.coo_matrix( ( data, ( i, j ) ) )
        
        sys.stderr.write("%d model reactions had zero reagents\n" %(numZeroReagents))
        sys.stderr.write("len i=%d len j=%d len data=%d\n" %(len(i), len(j), len(data)))
        sys.stderr.write("S_matrix rows=%d, cols=%d\n" %(S_matrix.shape[0], S_matrix.shape[1]))
    
        rxncosts = numpy.array(costs)    
        '''
        Given an S matrix (sparse) S, and a vector of reaction 
        probabilities rxnprobs, calls the scipy least-squares solver
        to obtain our best estimate for the metabolite weights
        (gamma)
     
        The reaction weights (probabilities) and metabolite weights
        are related by the equation
        R = |S|^T * gamma
     
        where R is the vector of reaction weights, |S| means the absolute value
        of S and gamma is the vector of metabolite weights. Solving in the least-squares
        sense is the best we can do since S is not a square matrix.
     
        Returns a list of metabolite weights with the same indexing as the rows of S.
        '''
         
        S_prime = S_matrix.transpose(copy=True)
        res = linalg.lsqr(S_prime, rxncosts)
        
        for index in range(len(res[0])):
            cpd = model["modelcompounds"][index]
            cpd["weight"] = res[0][index]
            
        return True
    
    def _checkDatabaseFiles(self):
        '''Check the status of the static database files.

        Raises NotReadyError if the database has not been loaded correctly.
        '''
        try:
            status = readStatusFile(self.config)
            if status != "ready":
                raise NotReadyError("Static database files are not ready.  Current status is '%s'." %(status))
        except IOError:
            statusFilePath = os.path.join(self.config["data_folder_path"], StatusFiles["status_file"])
            raise NotReadyError("Static database files are not ready.  Failed to open status file '%s'." %(statusFilePath))

    def _checkIfDatabaseFilesExist(self):
        '''
        Check for existence of all of the database files (needed if we cannot connect to Shock - particularly for testing)
        '''
        for key in DatabaseFiles:
            localPath = os.path.join(self.config["data_folder_path"], DatabaseFiles[key])
            if not os.path.exists(localPath):
                raise NotReadyError("Config file specified not to connect to Shock but database files not found from alternative method")
        return

    def _loadDatabaseFiles(self):
        '''Load the static database files from Shock.

        Reads the location of the static files from the deployment config file.
        '''
        
        # Get the current info about the static database files from the cache file.
        cacheFilename = os.path.join(self.config["data_folder_path"], StatusFiles["cache_file"])
        if os.path.exists(cacheFilename):
            fileCache = json.load(open(cacheFilename, "r"))
        else:
            fileCache = { }
        
        # Create a shock client.
        shockClient = ShockClient(self.config["shock_url"])

        # See if the static database files on this system are up-to-date with files stored in Shock.
        for key in DatabaseFiles:
            # Get info about the file stored in Shock.
            query = "lookupname=ProbAnnoData/"+DatabaseFiles[key]
            nodelist = shockClient.query(query)
            if len(nodelist) == 0:
                raise MissingFileError("Database file %s is not available from %s\n" %(DatabaseFiles[key], self.config["shock_url"]))
            node = nodelist[0]
            
            # Downlaod the file if the checksum does not match or the file is not available on this system.
            localPath = os.path.join(self.config["data_folder_path"], DatabaseFiles[key])
            download = False
            if key in fileCache:
                if node["file"]["checksum"]["sha1"] != fileCache[key]["file"]["checksum"]["sha1"]:
                    download = True
            else:
                download = True
            if os.path.exists(localPath) == False:
                download = True
            if download:
                sys.stderr.write("Downloading %s to %s\n" %(key, localPath))
                shockClient.download_to_path(node["id"], localPath)
                fileCache[key] = node
                
        # Save the updated cache file.
        json.dump(fileCache, open(cacheFilename, "w"), indent=4)
        return
     
    #END_CLASS_HEADER

    def __init__(self, config): #config contains contents of config file in hash or 
                                #None if it couldn't be found
        #BEGIN_CONSTRUCTOR
        '''Constructor for ProbabilisticAnnotation object.

        config: Contents of a config file in a hash. The config file should look like the deploy.cfg file
        but be in the location pointed to by KB_DEPLOYMENT_OONFIG
        '''
        if config == None:
            # There needs to be a config for the server to work.
            raise ValueError("__init__: A valid configuration was not provided.  Check KB_DEPLOYMENT_CONFIG and KB_SERVICE_NAME environment variables.")
        else:
            self.config = config
        
        # Convert flag to boolean value (a number greater than zero or the string 'True' turns the flag on).
        if self.config["debug"].isdigit():
            if int(self.config["debug"]) > 0:
                self.config["debug"] = True
            else:
                self.config["debug"] = False
        else:
            if self.config["debug"] == "True":
                self.config["debug"] = True
            else:
                self.config["debug"] = False
            
        # Create the data folder if it does not exist.
        if not os.path.exists(config["data_folder_path"]):
            os.makedirs(config["data_folder_path"], 0775)

        # See if the static database files are available.
        # Check the status
        # catch not ready error, when caught call method to download files from shock
        # or how about always call method to check cache and download as needed, catch error reading from cache file
        writeStatusFile(config, "running")
        try:
            self._loadDatabaseFiles()
            status = "ready"
        except:
            try:
                # We could still have already downloaded the files from somewhere else. Lets see if they all exist and are found in the expected location.
                sys.stderr.write("WARNING: Unable to get files from Shock. We will see if you have the static files from somewhere else but they might not be the latest!\n Caught error:\n")
                traceback.print_exc(file=sys.stderr)
                self._checkIfDatabaseFilesExist()
                status = "ready"
            except:
                try:
                    # Attempting fallback 2 - can we get the files from somewhere else?
                    sys.stderr.write("WARNING: Unable to get files from Shock. Attempting a fallback... (note this might not be the latest version of the files)\n")
                    os.system("wget %s" %(config["fallback_url"]))
                    os.system("tar xzvf PROBANNO_STATIC_DATA.tar.gz -C %s" %(os.path.abspath(os.path.join(config["data_folder_path"], ".."))))
                    os.system("rm PROBANNO_STATIC_DATA.tar.gz")
                    self._checkIfDatabaseFilesExist()
                    status = "ready"
                except:
                    sys.stderr.write("Unable to find files in this way either.\n")
                    status = "failed"
                    sys.stderr.write("\nCaught exception...\n")
                    traceback.print_exc(file=sys.stderr)

        writeStatusFile(config, status)
            
        #END_CONSTRUCTOR
        pass

    def annotate(self, input):
        # self.ctx is set by the wsgi application class
        # return variables are: jobid
        #BEGIN annotate
        ''' Compute probabilistic annotations from the specified genome object.

        input is a dictionary that must contain the following keys:
        auth : Auth string
        genome: Name of genome object
        genome_workspace: Workspace from which to grab the genome object
        probanno: Name of probanno object to output
        probanno_workspace: Workspace to which to save the probanno object

        The following fields are optional:
        verbose: Print lots of messages on the progress of the algorithm

        The function returns a job ID for the probanno calculation job.
        '''

        input = self._checkInputArguments(input, 
                                          ["auth", "genome", "genome_workspace", "probanno", "probanno_workspace"],
                                          { "verbose" : False }
                                          )
        
        # Make sure the static database files are ready.
        self._checkDatabaseFiles()
        
        # Store the path to the config file so the job script can set the configuration.
        input["kb_deployment_config"] = os.environ["KB_DEPLOYMENT_CONFIG"]
        
        # Use the same parameters for both job queue methods.
        queueCommand = "pa-annotate " + input["genome"] + " " + input["probanno"]
        queueJobParams = { "type": "ProbAnno", "auth": input["auth"], "queuecommand": queueCommand, "jobdata": input }
        
        # Queue a job to run annotate command using the workspace scheduler.
        if self.config["job_queue"] == "scheduler":
            wsClient = workspaceService(self.config["workspace_url"])
            job = wsClient.queue_job(queueJobParams)
            jobid = job["id"]
            sys.stderr.write("Submitted job job.%d to the KBase queue\n" %(jobid))
        # Run the job on the local machine.
        else:
            jid = randint(1,1000000)
            queueJobParams["id"] = "localjob.%d" %(jid)
            jobDirectory = os.path.join(self.config["work_folder_path"], "jobs", "localjob.%d" %(jid))
            os.makedirs(jobDirectory, 0770)
            jsonFilename = os.path.join(jobDirectory, "jobfile.json")
            outputFilename = os.path.join(jobDirectory, "stdout.log")
            errorFilename = os.path.join(jobDirectory, "stderr.log")
            json.dump(queueJobParams, open(jsonFilename, "w"), indent=4)
            cmdline = "nohup %s %s >%s 2>%s &" %(self.config["annotate_job_script_path"], jobDirectory, outputFilename, errorFilename)
            status = os.system(cmdline)
            sys.stderr.write("Submitted local job %d using directory %s\n" %(jid, jobDirectory))
            jobid = "%d" %(jid)
    
        #END annotate

        #At some point might do deeper type checking...
        if not isinstance(jobid, basestring):
            raise ValueError('Method annotate return value jobid is not type basestring as required.')
        # return the results
        return [ jobid ]
        
    def calculate(self, input):
        # self.ctx is set by the wsgi application class
        # return variables are: output
        #BEGIN calculate
        ''' Compute reaction probabilities from a probabilistic annotation.

        input is a dictionary that must contain the following keys:
        auth : Auth string
        probanno: Name of probanno object to input
        probanno_workspace: Workspace from which to grab the probanno object
        rxnprobs: Name of reaction probability object
        rxnprobs_workspace: Workspace to which to save the rxnprobs object

        The following fields are optional:
        verbose: Print lots of messages on the progress of the algorithm
        '''

        # Sanity check on input arguments
        input = self._checkInputArguments(input, 
                                          ["probanno", "probanno_workspace", "auth", "rxnprobs", "rxnprobs_workspace"], 
                                          { "verbose" : False ,
                                            "template_model" : None,
                                            "template_model_workspace" : None
                                            }
                                          )

        # Make sure the static database files are ready.
        self._checkDatabaseFiles()
        
        # Create a workspace client.
        wsClient = workspaceService(self.config["workspace_url"])
        
        # Get the ProbAnno object from the specified workspace.
        getObjectParams = { "type": "ProbAnno", "id": input["probanno"], "workspace": input["probanno_workspace"], "auth": input["auth"] }
        probannoObject = wsClient.get_object(getObjectParams)
        if "version" not in probannoObject["metadata"][10] or probannoObject["metadata"][10]["version"] != ProbAnnoVersion:
            raise WrongVersionError("ProbAnno object version is not %d" %(ProbAnnoVersion))
        genome = probannoObject["data"]["genome"]
        
        # Create a temporary directory for storing intermediate files. Only used when debug flag is on.
        if self.config["debug"]:
            workFolder = tempfile.mkdtemp("", "%s-" %(genome), self.config["work_folder_path"])
        else:
            workFolder = None

        # TODO - When Chris's template model functions are ready, the function call and subsequent data manipulation to get the dictionaries
        # we need will go here
        if input["template_model"] is not None or input["template_model_workspace"] is not None:
            if not(input["template_model"] is not None and input["template_model_workspace"] is not None) :
                raise IOError("Template model workspace is required if template model ID is provided")
            else:
                raise NotImplementedError("Template model support is not yet implemented")
        
        # Calculate per-gene role probabilities.
        roleProbs = self._rolesetProbabilitiesToRoleProbabilities(input, genome, probannoObject["data"]["roleset_probabilities"], workFolder)
    
        # Calculate whole cell role probabilities.
        # Note - eventually workFolder will be replaced with a rolesToReactions call
        totalRoleProbs = self._totalRoleProbabilities(input, genome, roleProbs, workFolder)
        
        # Calculate complex probabilities.
        # NOTE - when we have a roles_to_reactions function (or a reactions_to_roles would probably be better...) we need to
        # make a dictionary from complexes to their roles, and then call this function with a non-None value in
        # complexesToRequiredRoles
        complexProbs = self._complexProbabilities(input, genome, totalRoleProbs, workFolder, complexesToRequiredRoles = None)
        
        # Calculate reaction probabilities.
        # NOTE - when we have a roles_to_reactions function (or a reactions_to_roles would probably be better...) we need to
        # make a dictionary from reactions to their complexes, and then call this function with a non-None value in
        # rxnsToComplexes.
        reactionProbs = self._reactionProbabilities(input, genome, complexProbs, workFolder, rxnsToComplexes = None)

        if input["template_model"] is None:
            # Convert kb| IDs to modelSEED IDs
            EntityAPI = CDMI_EntityAPI(self.config["cdmi_url"])
            for ii in range(len(reactionProbs)):
                rxnid = reactionProbs[ii][0]
                rxndata = EntityAPI.get_entity_Reaction( [ rxnid ], [ "source_id" ] )
                reactionProbs[ii][0] = rxndata[rxnid]["source_id"]
 
        # Create a reaction probability object
        rxnProbObject = {}
        rxnProbObject["version"] = RxnProbsVersion
        rxnProbObject["genome"] = probannoObject["data"]["genome"]
        rxnProbObject["template_model"] = input["template_model"]
        rxnProbObject["probanno"] = probannoObject["data"]["id"]
        rxnProbObject["id"] = input["rxnprobs"]
        rxnProbObject["reaction_probabilities"] = reactionProbs

        objectMetadata = { "version": RxnProbsVersion, "num_reaction_probs": len(rxnProbObject["reaction_probabilities"]) }

        # Save output to the output workspace
        saveObjectParams = { "id" : input["rxnprobs"],
                             "type" : "RxnProbs",
                             "data" : rxnProbObject,
                             "metadata" : objectMetadata,
                             "workspace" : input["rxnprobs_workspace"],
                             "command" : "pa-calculate",
                             "auth" : input["auth"]
                             }

        output = wsClient.save_object(saveObjectParams)
        
        #END calculate

        #At some point might do deeper type checking...
        if not isinstance(output, list):
            raise ValueError('Method calculate return value output is not type list as required.')
        # return the results
        return [ output ]
        
    def get_rxnprobs(self, input):
        # self.ctx is set by the wsgi application class
        # return variables are: output
        #BEGIN get_rxnprobs

        # Sanity check on input arguments
        input = self._checkInputArguments(input, 
                                          ["auth", "rxnprobs", "rxnprobs_workspace"], 
                                          {}
                                          )

        wsClient = workspaceService(self.config["workspace_url"])
        getObjectParams = { "id"        : input["rxnprobs"],
                            "type"      : "RxnProbs",
                            "workspace" : input["rxnprobs_workspace"],
                            "auth"      : input["auth"]
                            }

        rxnProbsObject = wsClient.get_object(getObjectParams)
        output = rxnProbsObject["data"]["reaction_probabilities"]
        #END get_rxnprobs

        #At some point might do deeper type checking...
        if not isinstance(output, list):
            raise ValueError('Method get_rxnprobs return value output is not type list as required.')
        # return the results
        return [ output ]
        
