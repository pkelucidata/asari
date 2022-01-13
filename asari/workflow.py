'''
We use a similar concept of FeatureMap as in OpenMS here, 
but the correspondence algorithms take adavantage of high m/z resolution first, 
then utilizes MS1_pseudo spectra and cumulative elution profiles.

Steps in corespondence 
to move to constructors.py



'''
import os
import random

from metDataModel.core import Experiment

from .samples import SimpleSample
from .constructors import CompositeMap

from .sql import *

# General data processing steps are in this class
class ext_Experiment(Experiment):
    '''
    Extend metDataModel.core.Experiment with preprocessing methods.
    This encapsulates a set of LC-MS files using the same method to be processed together.
    '''
    def __init2__(self, list_input_files, dict_meta_data, parameters, output_dir):
        '''
        This is the overall container for all data in an experiment/project.
        Samples are sorted by name and assigned unique IDs.

        Input
        -----
        list_input_files: list of inputfiles, including directory path, to read
        dict_meta_data: description of sample types for each file, e.g. 'QC', 'pooled', 'sample'.
        parameters: including 'ionization_mode', 'min_intensity_threshold', 'min_timepoints'. See main.py.

        '''
        self.list_input_files = sorted(list_input_files)        # ordered by name
        self.output_dir = output_dir
        self.number_of_samples = len(list_input_files)

        self.files_meta_data = dict_meta_data
        
        self.parameters = parameters
        self.max_rtime = parameters['max_rtime']
        self.mode = parameters['mode']

        self.initiation_samples = self.__choose_initiation_samples__()

        # SAMPLE_REGISTRY
        self.samples = []                   # list of Sample instances
        self.samples_by_id = {}             # sample ID: Sample instance
        self.samples_by_name = {}           # input file name: Sample instance

        
    def process_all(self):
        '''
        
        if refDB:
            self.CMAP.align_to_refdb(refDB)

        '''
        # start SQLite database
        self.cursor = connect_sqlite_db(self.parameters['project_name'])
        
        self.CMAP = CompositeMap(self)
        initiation_Samples = self.process_initiation_samples()
        self.CMAP.initiate_mass_grid( initiation_Samples )

        for f in self.list_input_files:                                 # run remaining samples, 
            if f not in self.initiation_samples:
                SM = self.process_single_sample(f)
                # not via DB
                self.CMAP.add_sample(SM)

        self.CMAP.set_RT_reference()
        self.global_peak_detection(self.CMAP)


    def process_initiation_samples(self):
        init_sm = [self.process_single_sample(f) for f in self.initiation_samples]
        return [SM for SM in init_sm if SM]

    def process_single_sample(self, input_file):
        '''

        To add DB function in HERE
        '''
        try:
            SM = SimpleSample(self, self.mode, input_file)
            SM.process()
            # sample id, assigned by index in self.list_input_files.
            # DB commit
            return SM
        except IndexError:
            print("Input error in sample %s, dropped from processing." %f)
            return None
        


    #---------------------------------------------------------------------------------------------------------------

    def __obsolete__process_all(self):
        '''
        This will shift to a DB design in next version.
        '''
        self.init_hot_db( self._get_ref_db_() )                         # initial processing of 3 samples to set up HOT_DB
        for f in self.list_input_files:                                 # run remaining samples
            if f not in self.initiation_samples:
                SM = Sample(self, self.mode, f)
                SM.process_step_1()
                SM.process_step_2(self.HOT_DB)
                if not self.parameters['cache_mass_traces']:
                    del(SM.dict_masstraces)
                self.samples.append(SM)
        
        self.calibrate_retention_time()                                 # samples may be marked to drop
        self.correspondence()
        self.annotate_final()
        self.export_feature_table(self.FeatureTable, self.parameters['output_filename'])
        
    def _get_ref_db_(self):
        '''
        Dispatch for ref DB.
        Earlier version used INIT_DFDB = DB_to_DF( extend_DB1(DB_1) ), which was moved to mass2chem.
        '''
        if self.mode == 'pos':
            dbfile = os.path.join(os.path.dirname(__file__), 'ref_db_v0.2.tsv')
        elif self.mode == 'neg':
            dbfile = os.path.join(os.path.dirname(__file__), 'neg_ref_db_v0.2.tsv')
        else:
            print("Ionization mode is either `pos` or `neg`.")
        return tsv2refDB(dbfile)


    def init_hot_db(self, DFDB):
        '''
        Use three samples to initiate a hot DB to house feature annotation specific to this Experiment, and speed up subsequent search.
        The HOT_DB will be used during sample processing, and have another update after correspondence and additional annotation.
        The HOT_DB will then be exported as Expt annotation.
        '''
        chosen_Samples, found_formula_masses = [], []
        for f in self.initiation_samples:
            SM = Sample(self, self.mode, f)
            SM.process_step_1()
            SM.process_step_2(DFDB)
            chosen_Samples.append(SM)
            found_formula_masses += list(SM.mzstr_2_formula_mass.values())

        self.samples += chosen_Samples
        # Experiment wide parameters
        self.__mass_stdev__ = np.median([SM.__mass_stdev__ for SM in chosen_Samples])       # ppm stdev, used for later searches
        # ver 1 HOT_DB will be a subset of INIT_DFDB

        found_formula_masses = set(found_formula_masses)
        if None in found_formula_masses:
            found_formula_masses.remove(None)
        self.HOT_DB = DFDB.loc[found_formula_masses]   

        print("\n[@.@] Anchoring with %d initial formula matches." %len(found_formula_masses))
        print("[@.@] Initial estimation done on\n" + '\n'.join(self.initiation_samples))
        #export_hot_db, without last col
        self.HOT_DB.iloc[:, :-1].to_csv(os.path.join(self.output_dir, '__intermediary__' + self.parameters['annotation_filename']), sep="\t")
        


    def calibrate_retention_time(self, method='spline', smoothing_factor=0.5):
        '''
        Calibrate (align) RT using selected `good` peaks.
        Overlay all samples to take median values as reference RT for each peak.

        method:spline:
        Do Spline regression of each sample against the reference (composite),
        and apply the spline function to all RTs in the sample as calibrated RT.
        https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.UnivariateSpline.html

        method:dtw:
        https://dynamictimewarping.github.io/
        Will compare with spline later, and implement if desired (?).    

        '''
        rt_table = self.get_rt_calibration_ref()    # This is the pd.DataFrame containing peak data for RT calibration
        rt_table['median'] = rt_table.median(axis=1)
        # rt_table = rt_table.sort_values(by='median')
        # rtbins = np.linspace(0, self.max_rtime, 11)
        for SM in self.samples:
            rt_cal = rt_table[[SM.name, 'median']].dropna(axis=0, how='any').values.tolist() 
            # now this is listed converted from numpy.ndarray 
            if len(rt_cal) < self.parameters['peak_number_rt_calibration']:
                SM.__valid__ = False 
                print("\n\n*** Warning, RT regression using too few features (%d) ***" %len(rt_cal))
                print("*** Sample %s removed from processing. ***\n\n" %SM.name)
            else:
                rt_cal.sort()
                # to-do: need down sample to spread better over rt range
                xx, yy = [0, ], [0, ]                   # force left to 0
                for L in rt_cal:
                    if abs(L[0]-L[1])/L[1] < 0.2:       # control shift < 20%
                        xx.append(L[0])
                        yy.append(L[1])

                # force right match, to avoid erradic spline running out of sanity
                right_end = 1.1 * max( self.parameters['max_rtime'], L[0], L[1] )
                xx.append(right_end)
                yy.append(right_end)
                # leave out s=smoothing_factor
                spl = UnivariateSpline(xx, yy, )
                SM.__rt_calibration__ = spl
                SM.__rt_calibration__data__ = (xx, yy)

                # calibrate all detected RT for all peaks, and raw RT from MassTraces
                for P in SM.good_peaks:
                    P.cal_rtime = SM.__rt_calibration__(P.rtime)
                    P.left_rtime = SM.__rt_calibration__(P.left_rtime)
                    P.right_rtime = SM.__rt_calibration__(P.right_rtime)

    def get_rt_calibration_ref(self):
        '''
        Get N good peaks per sample, single peaks in mass_trace with R^2 > 0.9;
        consistently present in most samples (70%).
        RT variation less than 20% of RT range. !!! important !!! - because we don't fix if chromatography is too bad.
        No imputation solution for a peak missing in a sample. Because one can't assume the peak RT has no shift when the next peak may do.
        Therefore, it's better to skip the peak altogether for that sample.

        return
        ------
        A pd.DataFrame with RT values as reference for selected features.
        '''
        # get reference features for RT calibration/alignment
        d = {}
        for SM in self.samples:
            # SM.export_peaklist()  # test
            good_peaks_rtime, good_peaks_formula_mass = [], []
            for P in SM.good_peaks:                                    # selectivity rules out redundant formulae
                if P.mzstr in SM.mzstr_2_formula_mass and P.selectivity > 0.98 and P.goodness_fitting > 0.9:
                    good_peaks_rtime.append( P.rtime )
                    good_peaks_formula_mass.append(SM.mzstr_2_formula_mass[P.mzstr])

            d[SM.name] = pd.Series(good_peaks_rtime, index=good_peaks_formula_mass)
        
        rt_table = pd.DataFrame(d)      # merge into a table, each row as feature, col as sample
        # avoiding pd.DataFrame whenever possible, unpredictable behaivors
        # drop rows by min presence in > 50% of samples. Not significnat, but potentially tricky for very large studies. 
        # QC or blank samples may behave differently
        rt_table = rt_table.dropna(axis=0, thresh=min(int(0.5 * self.number_of_samples), 10))
        rt_table.to_csv("raw_rt_calibration_matrix.tsv", sep="\t")    # to export 
        return rt_table
        
    def correspondence(self):
        '''
        In each sample: Peak.mzstr links to Sample.mzstr_2_formula_mass, Sample.dict_masstraces
        Start feature table using good peaks (quality > 0.8), then fill weak peaks based on them. 
        Because no feature should be considered if no single good peak exists.
        To do: detailed peak info will be pushed in to SQLite DB.
        '''
        self.samples = [SM for SM in self.samples if SM.__valid__]      # remove sample!!!
        self.ordered_sample_names = [SM.name for SM in self.samples]    # used to order intensity values in Features 
        unassigned = []
        peak_dict = {}
        for SM in self.samples:
            self.name_to_Sample[SM.name] = SM
            for P in SM.good_peaks:
                #if P.goodness_fitting > 0.9:
                if P.mzstr not in SM.mzstr_2_formula_mass:              # next to get a label of consensus m/z
                    unassigned.append((P.mz, P.rtime, P))               # need rtime to break ties in sorting
                else:                                                   # those with formula_mass labelled
                    k = SM.mzstr_2_formula_mass[P.mzstr]
                    if k in peak_dict:
                        peak_dict[k].append(P)
                    else:
                        peak_dict[k] = [P]

        if unassigned:
            unassigned.sort()
            unassigned = [(x[0], x[2]) for x in unassigned]
            mz_peak_bins = bin_by_median(unassigned, lambda x: 2 * self.__mass_stdev__ * 0.000001 * x)
            for BIN in mz_peak_bins:
                peak_dict[ '_M_' + str(round(np.median([P.mz for P in BIN]),6)) ] = BIN
        FeatureList = peaks_to_features(peak_dict, self.parameters['rtime_tolerance'], self.ordered_sample_names)
        print("Additional features are assembled based on 2x stdev (%5.2f ppm) seen in this experiment, " % self.__mass_stdev__)
        self.FeatureTable = FeatureList
        # to update selectivity_combined

    def annotate_final(self):
        '''
        More formula annotation of _M_ features using HMDB+PubChemLite;
        Group into empCpds via mass2chem.


        Still to do remaining features
        '''
        s = u'\t'.join(['feature_id', 'formula_mass', 'mz_dbrecord',	'intensity_mean', 'charged_formula', 'selectivity',	'neutral_formula_mass',
                                    'ion_relation', 'id_HMDB', 'name']) + '\n'
        for F in self.FeatureTable:
            if "_M_" == F.mass_id[:3]:
                s += u'\t'.join([F.feature_id, F.mass_id, str(round(F.mz,4)), str(F.intensity_mean)]) + '\n'
            else:
                [mz, charged_formula, selectivity, neutral_formula_mass, ion_relation] = [str(x) for x in list(self.HOT_DB.loc[F.mass_id])[:5]]
                name = massDict_hmdb.get(neutral_formula_mass, '')
                if name:
                    name = u'\t'.join( [';'.join(x) for x in name] ).encode('utf-8', 'ignore').decode('utf-8')
                s += u'\t'.join([F.feature_id, F.mass_id, mz, str(F.intensity_mean),
                                charged_formula, selectivity, neutral_formula_mass, ion_relation, name]) + '\n'
                
        with open(os.path.join(self.output_dir, self.parameters['annotation_filename']), 'w', encoding='utf-8') as O:
            O.write(s.encode('utf-8', 'ignore').decode('utf-8'))

        

    def export_feature_table(self, FeatureList, outfile='feature_table.tsv'):
        '''
        FeatureList: a list of namedTuples, i.e. Features; Output two files, one main, another low quality features.
        '''
        def __write__(FeatureList, outfile):
            s = '\t'.join(['feature_id', 'formula_mass', 'mz', 'rtime', 'rt_min', 'rt_max', 'number_peaks',
                                    'peak_quality_max', 'peak_quality_median', 'intensity_mean', 'selectivity_mz',
                                    ] + self.ordered_sample_names) + '\n'
            for F in FeatureList:
                s += '\t'.join(
                    [F.feature_id, F.mass_id, str(round(F.mz,4)), str(round(F.rtime,2)), str(round(F.rt_min,2)), str(round(F.rt_max,2)), str(F.number_peaks),
                    str(round(F.peak_quality_max,2)), str(round(F.peak_quality_median,2)), str(F.intensity_mean), str(round(F.selectivity_mz,2)),
                    ] + [str(int(x)) for x in F.intensities]
                    ) + '\n'
            with open( outfile, 'w') as O:
                O.write(s)

        high_quality_features, low_quality_features = [], []
        for F in FeatureList: 
            if F.peak_quality_max > 0.8 and F.perc_peaks > 15:
                high_quality_features.append(F)
            else:
                low_quality_features.append(F)

        high_quality_features.sort(key=lambda F: F.peak_quality_max, reverse=True)
        #print(FeatureList[99])
        __write__(high_quality_features, os.path.join(self.output_dir, outfile))
        __write__(low_quality_features, os.path.join(self.output_dir, 'low_quality_features_' + outfile))
        print("Feature tables were written under %s." %self.output_dir)
        print("The main feature table (%s) has %d samples and %d features.\n\n\n" %(
            self.parameters['output_filename'], len(self.ordered_sample_names), len(high_quality_features)))




    def __choose_initiation_samples__(self):
        '''

        To consider using meta data??
            elif not self.files_meta_data:
            else:
                chosen = []
                POOLED = [f for f in self.list_input_files if self.files_meta_data[f] == 'POOLED']
                if POOLED:
                    chosen.append(random.choice(POOLED))
                QC = [f for f in self.list_input_files if self.files_meta_data[f] == 'QC']
                if QC:
                    chosen.append( random.choice(QC) )
                OTHERS = [f for f in self.list_input_files if self.files_meta_data[f] not in ['POOLED', 'QC', 'BLANK']]
                chosen += random.sample(OTHERS, 3)
                return chosen[:3]
        
        '''
        if self.parameters['initiation_samples']:
            return self.parameters['initiation_samples']
        else:
            if self.number_of_samples < 4:
                return self.list_input_files
            else:
                return random.sample(self.list_input_files, 3)
