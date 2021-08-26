'''
Reusing class definitions in metDataModel.

The flow in asari:
    Get MassTraces
    Peak detection per MassTrace
    Mass calibration
    Retention indexing
    Correspondence
    Output feature table
    (Further annotation is done via mass2chem)

Examples of data
----------------
Peak: {'id': self.id, 
                'mz': self.mz, 
                'rtime': self.rtime, 
                'ms_level': self.ms_level,
                'ionization': self.ionization,
                'list_mz': self.list_mz,
                'list_retention_time': self.list_retention_time,
                'list_retention_time_corrected': self.list_retention_time_corrected,
                'list_intensity': self.list_intensity,
                }

massTrace: {'id': self.id, 
                'mz': self.mz, 
                'list_mz': self.list_mz,
                'list_retention_time': self.list_retention_time,
                'list_intensity': self.list_intensity,
                }

'''

import numpy as np
from scipy.signal import find_peaks  
from scipy.optimize import curve_fit                                                                        

from pyopenms import MSExperiment, MzMLFile

from metDataModel.core import massTrace, Peak, Feature, Experiment
from mass2chem.annotate import mass_calibrate, calibration_mass_dict_pos, mass_formula_annotate, DB_1, DB_2, DB_3


def __gaussian_function__(x, a, mu, sigma):
    return a*np.exp(-(x-mu)**2/(2*sigma**2)) 

def __goodness_fitting__(y_orignal, y_fitted):
    # R^2 as goodness of fitting
    return 1 - (np.sum((y_fitted-y_orignal)**2) / np.sum((y_orignal-np.mean(y_orignal))**2))


class ext_Experiment(Experiment):
    '''
    Extend metDataModel Experiment with preprocessing methods.

    '''

    def set_sample_order(self, list_samples):
        # leave sort decision elsewhere
        self.sample_order = list_samples
        self.samples = []

    def assign_formula_masses(self):
        for SM in self.samples:
            SM.calibrate_mass()
            SM.assign_formula_mass
        

    def calibrate_retention_time(self, N=50, smoothing_factor=2):
        '''
        Get N good peaks per sample, single peaks in mass_trace with R^2 > 0.9;
        consistently present in almost all samples, 
        spread out through RT range (selection by 10 bins);
        RT variation less than 20% of RT range.
    
        Overaly all samples to take median values as reference RT for each peak.
        Do Spline regression of each sample against the reference (composite),
        and apply the spline function to all RTs in the sample as calibrated RT.
        Skip a sample if not enough "good peaks" are found.
        
        https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.UnivariateSpline.html

        '''
        pass


    def correspondency(self):
        '''
        '''
        pass



    def export_feature_table(self, outfile='feature_table.tsv'):
        '''
        Input
        -----
        list_intensityxxxx

        Return
        ------
        list of Peaksxxxxxxxx
        '''
        new = list(set(L))
        new.sort()
        new = [str(round(x,6)) for x in new]
        with open(outfile, 'w') as O:
            O.write('\n'.join(new))


class Sample:
    '''
    Peak detection is performed at mass trace level.
    Mass calibration and annotating formula_mass is at sample level.
    Retention time calibration and correspondency is done across samples at experiment level.
    '''
    def __init__(self, input_file=''):
        self.input_file = input_file
        self.belonged_experiment = ''
        # fixed sequence
        self.list_MassTraces = []

    def detect_peaks(self, 
                    min_intensity_threshold=30000, 
                    min_timepoints=2,
                    min_prominence_threshold=10000,
                    prominence_window=30
                    ):
        list_MassTraces = self.read_chromatogram_file(self.input_file, min_intensity_threshold)
        for M in list_MassTraces:
            M.detect_peaks(min_intensity_threshold, min_timepoints, min_prominence_threshold, prominence_window) 
            if M.list_peaks:
                self.list_MassTraces.append(M)

        self.number_MassTraces = len(self.list_MassTraces)
        print("After peak detection, got %d valid mass traces." %self.number_MassTraces)

    def read_chromatogram_file(self, infile, min_intensity_threshold):
        '''
        Get chromatograms, via pyOpenMS functions. 
        Convert to list MassTraces for transparency.
        The rest of software uses metDataModel class extensions.

        Input
        -----
        An mzML file of chromatograms (i.e. EIC or XIC, or mass trace).
        Currently using a massTrace file produced by FeatureFinderMetabo (OpenMS). 
        min_intensity_threshold: minimal intensity requirement for a mass trace to be considered. 
        Default value 10,000 is lenient in our Orbitrap data.

        Return
        ------
        A list of MassTrace objects.

        '''
        exp = MSExperiment()                                                                                          
        MzMLFile().load(infile, exp)
        list_MassTraces = []
        mzDict = {}
        for chromatogram in exp.getChromatograms():
            mz = chromatogram.getPrecursor().getMZ()
            # remove redundancy. Repeated m/z values possible when big peaks spill over.
            mz_str = str(round(mz,6))
            RT, INTS = chromatogram.get_peaks() 
            if INTS.max() > min_intensity_threshold:
                if mz_str not in mzDict or INTS.sum() > mzDict[mz_str][2].sum():
                    mzDict[mz_str] = [mz, RT, INTS]

        for [mz, RT, INTS] in mzDict.values():
            M = ext_MassTrace()
            M.mz, M.list_retention_time, M.list_intensity = [mz, RT, INTS]
            list_MassTraces.append( M )

        print("Processing %s, found %d mass traces." %(self.input_file, len(list_MassTraces)))
        return list_MassTraces

    def calibrate_mass_formula(self):
        _pesudo_features = [{'mz': M.mz} for M in self.list_MassTraces]
        _pram, new_list = mass_calibrate(_pesudo_features, calibration_mass_dict_pos, 20)
        ii = 0
        for F in new_list:
            self.list_MassTraces[ii].calibrated_mz = F['calibrated_mz']
            self.list_MassTraces[ii].mass_formula = mass_formula_annotate(F['calibrated_mz'])
            ii += 1


class ext_Peak(Peak):
    '''
    Extending metDataModel.core.Peak.
    Include pointer to parent MassTrace.
    Not storing RT or intensity arrays, but looking up in MassTrace.
    '''
    def peak_initiate(self, parent_mass_trace, mz, apex, peak_height, left_base, right_base):
        [ self.parent_mass_trace, self.mz, self.apex, self.peak_height, self.left_base, self.right_base
                ] = [ parent_mass_trace, mz, apex, peak_height, left_base, right_base ]

    def evaluate_peak_model(self):
        '''
        Use Gaussian models to fit peaks, R^2 as goodness of fitting.
        Peak area is defined by Gaussian model,
        the integral of a Gaussian function being a * c *sqrt(2*pi).

        Left to right base may not be full represenation of the peak. 
        The Gaussian function will propose a good boundary.
        '''
        xx = self.parent_mass_trace.list_retention_time[self.left_base: self.right_base+1]
        # set initial parameters
        a, mu, sigma =  self.peak_height, \
                        self.parent_mass_trace.list_retention_time[self.apex], \
                        xx.std()
        try:
            popt, pcov = curve_fit(__gaussian_function__, 
                            xx,
                            self.parent_mass_trace.list_intensity[self.left_base: self.right_base+1],
                            p0=[a, mu, sigma])

            self.gaussian_parameters = popt
            self.rtime = popt[1]
            self.peak_area = popt[0]*popt[2]*2.506628274631
            self.goodness_fitting = __goodness_fitting__(
                            self.parent_mass_trace.list_intensity[self.left_base: self.right_base+1], 
                            __gaussian_function__(xx, *popt))
        # failure to fit
        except RuntimeError:
            self.rtime = mu
            self.peak_area = a*sigma*2.506628274631
            self.goodness_fitting = 0


    def extend_model_range(self):
        # extend model xrange, as the initial peak definition may not be complete, for visualization
        _extended = self.right_base - self.left_base
        # negative index does not work here, thus max to 0
        self.rt_extended = self.parent_mass_trace.list_retention_time[
                                            max(0, self.apex-_extended): self.apex+_extended]
        self.y_fitted_extended = __gaussian_function__(self.rt_extended, *self.gaussian_parameters)

    def serialize2(self):
        pass


class ext_MassTrace(massTrace):
    '''
    Extending metDataModel.core.massTrace

    self.calibrated_mz - will update at calibration

    Peak detection using scipy.signal.find_peaks, a local maxima method with prominence control.
    Example
    -------

    peaks:    (array([10, 16]),
    properties:    {'peak_heights': array([3736445.25, 5558352.5 ]),
                    'prominences': array([1016473.75, 3619788.  ]),
                    'left_bases': array([0, 6]),
                    'right_bases': array([12, 22]),
                    'widths': array([3.21032149, 5.58696106]),
                    'width_heights': array([3228208.375, 3748458.5  ]),
                    'left_ips': array([ 8.12272812, 13.25125172]),
                    'right_ips': array([11.33304961, 18.83821278])},
    mz:    786.599123189169,
    list_retention_time:    array([66.26447883, 66.63433189, 67.01434098, 67.38420814, 67.7534237 ,
                    68.12477678, 68.49404578, 68.87324949, 69.25232779, 69.63137115,
                    70.01254658, 70.39144237, 70.77048133, 71.14956203, 71.52869926,
                    71.90967366, 72.27886773, 72.65788714, 73.02685195, 73.39578426,
                    73.76490146, 74.13592309, 74.51518654]),
    list_intensity:    array([  24545.924,   40612.44 ,  151511.27 ,  343555.5  ,  885063.8  ,
                    1232703.6  , 1938564.5  , 2306679.2  , 3195485.5  , 3462114.5  ,
                    3736445.2  , 3482002.5  , 2719971.5  , 3400839.8  , 4784387.5  ,
                    4500411.   , 5558352.5  , 4896509.   , 5039317.5  , 3499304.   ,
                    2457701.2  , 1694263.9  , 1377836.1  ], dtype=float32))

    min_prominence_threshold is unlikely one-size fits all. But an iterative detection will work better.
    prominence_window is not important, but is set to improve performance.
    min_timepoints is taken at mid-height, therefore 2 is set to allow very narrow peaks.
    gaussian_shape is minimal R^2 in Gaussian peak goodness_fitting.
    '''

    def detect_peaks(self, 
                    min_intensity_threshold=30000, 
                    min_timepoints=2,
                    min_prominence_threshold=10000,
                    prominence_window=30,
                    gaussian_shape=0.8,
                    ):
        self.list_peaks = []
        peaks, properties = find_peaks(self.list_intensity, 
                                    height=min_intensity_threshold, width=min_timepoints, 
                                    prominence=min_prominence_threshold, wlen=prominence_window) 
        
        if len(peaks) > 1:
            # Rerun, raising min_prominence_threshold. Not relying on peak shape for small peaks, 
            # because chromatography is often not good so that small peaks can't be separated from noise.
            _tenth_height = 0.1*self.list_intensity.max()
            min_prominence_threshold = max(min_prominence_threshold, _tenth_height)
            peaks, properties = find_peaks(self.list_intensity, 
                                    height=min_intensity_threshold, width=min_timepoints, 
                                    prominence=min_prominence_threshold, wlen=prominence_window)
        
        for ii in range(len(peaks)):
            P = ext_Peak()
            # scipy.signal.find_peaks works on 1-D array. RT coordinates need to be added back.
            P.peak_initiate(parent_mass_trace=self, mz = self.mz, apex = peaks[ii],
                            peak_height = properties['peak_heights'][ii],
                            left_base = properties['left_bases'][ii],
                            right_base = properties['right_bases'][ii])
            P.evaluate_peak_model()
            if P.goodness_fitting > gaussian_shape:
                self.list_peaks.append(P)

        self.number_of_peaks = len(self.list_peaks)

    def serialize2(self):
        d = {
            'number_of_peaks': self.number_of_peaks,
            'peaks': [P.id for P in self.list_peaks],
        }
        return d.update( self.serialize() )
        

