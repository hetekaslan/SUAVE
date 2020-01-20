# Propulsor_Surrogate.py
#
# Created:  Mar 2017, E. Botero
# Modified:

# ----------------------------------------------------------------------
#  Imports
# ----------------------------------------------------------------------

# suave imports
import SUAVE

# package imports
import numpy as np
from copy import deepcopy
from SUAVE.Components.Propulsors.Propulsor import Propulsor
from SUAVE.Methods.Aerodynamics.Supersonic_Zero.Drag.Cubic_Spline_Blender import Cubic_Spline_Blender

from SUAVE.Core import Data
import sklearn
from sklearn import gaussian_process
from sklearn.gaussian_process.kernels import RationalQuadratic, ConstantKernel, RBF, Matern
from sklearn import neighbors
from sklearn import svm, linear_model

# ----------------------------------------------------------------------
#  Network
# ----------------------------------------------------------------------

## @ingroup Components-Energy-Networks
class Propulsor_Surrogate(Propulsor):
    """ This is a way for you to load engine data from a source.
        A .csv file is read in, a surrogate made, that surrogate is used during the mission analysis.
       
        You need to use build surrogate first when setting up the vehicle to make this work.
   
        Assumptions:
        The input format for this should be Altitude, Mach, Throttle, Thrust, SFC
       
        Source:
        None
    """        
    def __defaults__(self):
        """ This sets the default values for the network to function.
   
            Assumptions:
            None
   
            Source:
            N/A
   
            Inputs:
            None
   
            Outputs:
            None
   
            Properties Used:
            N/A
        """          
        self.nacelle_diameter         = None
        self.engine_length            = None
        self.number_of_engines        = None
        self.tag                      = 'Engine_Deck_Surrogate'
        self.input_file               = None
        self.sfc_surrogate            = None
        self.thrust_surrogate         = None
        self.thrust_angle             = 0.0
        self.areas                    = Data()
        self.surrogate_type           = 'gaussian'
        self.altitude_input_scale     = 1.
        self.thrust_input_scale       = 1.
        self.sfc_anchor               = None
        self.sfc_anchor_scale         = 1.
        self.sfc_anchor_conditions    = np.array([[1.,1.,1.]])
        self.thrust_anchor            = None
        self.thrust_anchor_scale      = 1.
        self.thrust_anchor_conditions = np.array([[1.,1.,1.]])
        self.sfc_rubber_scale         = 1.
        self.use_extended_surrogate   = False
   
    # manage process with a driver function
    def evaluate_thrust(self,state):
        """ Calculate thrust given the current state of the vehicle
   
            Assumptions:
            None
   
            Source:
            N/A
   
            Inputs:
            state [state()]
   
            Outputs:
            results.thrust_force_vector [newtons]
            results.vehicle_mass_rate   [kg/s]
   
            Properties Used:
            Defaulted values
        """            
       
        # Unpack the surrogate
        sfc_surrogate = self.sfc_surrogate
        thr_surrogate = self.thrust_surrogate
       
        # Unpack the conditions
        conditions = state.conditions
        # rescale altitude for proper surrogate performance
        altitude   = conditions.freestream.altitude/self.altitude_input_scale
        mach       = conditions.freestream.mach_number
        throttle   = conditions.propulsion.throttle
       
        cond = np.hstack([altitude,mach,throttle])
       
        ## Run the surrogate for a range of altitudes
        #data_len = len(altitude)
        #sfc = np.zeros([data_len,1])  
        #thr = np.zeros([data_len,1])
        #for ii,_ in enumerate(altitude):            
            #sfc[ii] = sfc_surrogate.predict([np.array([altitude[ii][0],mach[ii][0],throttle[ii][0]])])\
                #*self.sfc_input_scale*self.sfc_rubber_scale
            #thr[ii] = thr_surrogate.predict([np.array([altitude[ii][0],mach[ii][0],throttle[ii][0]])])\
                #*self.thrust_input_scale*self.thrust_rubber_scale
            
        if self.use_extended_surrogate:
            lo_blender = Cubic_Spline_Blender(0, .01)
            hi_blender = Cubic_Spline_Blender(0.99, 1)            
            sfc = self.extended_sfc_surrogate(sfc_surrogate, cond, lo_blender, hi_blender)
            thr = self.extended_thrust_surrogate(thr_surrogate, cond, lo_blender, hi_blender)
        else:
            sfc = sfc_surrogate.predict(cond)
            thr = thr_surrogate.predict(cond)

        sfc = sfc*self.sfc_input_scale*self.sfc_anchor_scale
        thr = thr*self.thrust_input_scale*self.thrust_anchor_scale
       
        F    = thr
        #from SUAVE.Core import Units
        mdot = thr*sfc*self.number_of_engines#*Units.lb/Units.lbf/Units.hr
       
        # Save the output
        results = Data()
        results.thrust_force_vector = self.number_of_engines * F * [np.cos(self.thrust_angle),0,-np.sin(self.thrust_angle)]    
        results.vehicle_mass_rate   = mdot
        results.tsfc                = sfc
        results.thrust_scalar_value = thr
   
        return results          
   
    def build_surrogate(self,my_data=None):
        """ Build a surrogate. Multiple options for models are available including:
            -Gaussian Processes
            -KNN
            -SVR
   
            Assumptions:
            None
   
            Source:
            N/A
   
            Inputs:
            state [state()]
   
            Outputs:
            self.sfc_surrogate    [fun()]
            self.thrust_surrogate [fun()]
   
            Properties Used:
            Defaulted values
        """          
       
        if my_data is None:
           
            # file name to look for
            file_name = self.input_file
           
            # Load the CSV file
            my_data = np.genfromtxt(file_name, delimiter=',')
           
            # Remove the header line
            my_data = np.delete(my_data,np.s_[0],axis=0)
           
            # Clean up to remove redundant lines
            b = np.ascontiguousarray(my_data).view(np.dtype((np.void, my_data.dtype.itemsize * my_data.shape[1])))
            _, idx = np.unique(b, return_index=True)
           
            my_data = my_data[idx]                
               
   
        xy  = my_data[:,:3] # Altitude, Mach, Throttle
        thr = np.transpose(np.atleast_2d(my_data[:,3])) # Thrust
        sfc = np.transpose(np.atleast_2d(my_data[:,4]))  # SFC
        
        self.altitude_input_scale = np.max(xy[:,0])
        self.thrust_input_scale   = np.max(thr)
        self.sfc_input_scale      = np.max(sfc)
        
        # normalize for better surrogate performance
        xy[:,0] /= self.altitude_input_scale
        thr     /= self.thrust_input_scale
        sfc     /= self.sfc_input_scale
       
       
        # Pick the type of process
        if self.surrogate_type  == 'gaussian':
            gp_kernel = Matern()
            regr_sfc = gaussian_process.GaussianProcessRegressor(kernel=gp_kernel,normalize_y=True)
            regr_thr = gaussian_process.GaussianProcessRegressor(kernel=gp_kernel)      
            thr_surrogate = regr_thr.fit(xy, thr)
            sfc_surrogate = regr_sfc.fit(xy, sfc)  
           
        elif self.surrogate_type  == 'knn':
            regr_sfc = neighbors.KNeighborsRegressor(n_neighbors=1,weights='distance')
            regr_thr = neighbors.KNeighborsRegressor(n_neighbors=1,weights='distance')
            sfc_surrogate = regr_sfc.fit(xy, sfc)
            thr_surrogate = regr_thr.fit(xy, thr)  
   
        elif self.surrogate_type  == 'svr':
            regr_thr = svm.SVR(C=500.)
            regr_sfc = svm.SVR(C=500.)
            sfc_surrogate  = regr_sfc.fit(xy, sfc)
            thr_surrogate  = regr_thr.fit(xy, thr)    
           
        elif self.surrogate_type == 'linear':
            regr_thr = linear_model.LinearRegression()
            regr_sfc = linear_model.LinearRegression()          
            sfc_surrogate  = regr_sfc.fit(xy, sfc)
            thr_surrogate  = regr_thr.fit(xy, thr)
            
        else:
            raise NotImplementedError('Selected surrogate method has not been implemented')
       
       
        if self.thrust_anchor is not None:
            cons = deepcopy(self.thrust_anchor_conditions)
            cons[0,0] /= self.altitude_input_scale
            base_thrust_at_anchor = thr_surrogate.predict(cons)
            self.thrust_anchor_scale = self.thrust_anchor/(base_thrust_at_anchor*self.thrust_input_scale)
            
        if self.sfc_anchor is not None:
            cons = deepcopy(self.sfc_anchor_conditions)
            cons[0,0] /= self.altitude_input_scale
            base_sfc_at_anchor = sfc_surrogate.predict(cons)
            self.sfc_anchor_scale = self.sfc_anchor/(base_sfc_at_anchor*self.sfc_input_scale)
       
        # Save the output
        self.sfc_surrogate    = sfc_surrogate
        self.thrust_surrogate = thr_surrogate   
        
    def extended_thrust_surrogate(self, thr_surrogate, X, lo_blender, hi_blender):
        # initialize
        X_zero_eta = deepcopy(X)
        X_one_eta = deepcopy(X)
        X_zero_eta[:,2] = 0
        X_one_eta[:,2] = 1
        min_thrs = thr_surrogate.predict(X_zero_eta)
        max_thrs = thr_surrogate.predict(X_one_eta)
        dTdetas = max_thrs - min_thrs
        etas = X[:,2]
        mask_low = etas < 0
        mask_lo_blend = np.logical_and(etas >= 0, etas < 0.01)
        mask_mid = np.logical_and(etas >= 0.01, etas < 0.99)
        mask_hi_blend = np.logical_and(etas >= 0.99, etas < 1)
        mask_high = etas >= 1
        
        etas = np.atleast_2d(etas).T
        T = np.zeros_like(etas)
        
        # compute thrust
        T[mask_low] = min_thrs[mask_low] + etas[mask_low]*dTdetas[mask_low]
        
        if np.sum(mask_lo_blend) > 0:
            lo_weight = lo_blender.compute(etas[mask_lo_blend])
            T[mask_lo_blend] = (min_thrs[mask_lo_blend] + etas[mask_lo_blend]*dTdetas[mask_lo_blend])*lo_weight + \
                               thr_surrogate.predict(X[mask_lo_blend])*(1-lo_weight)
        
        if np.sum(mask_mid) > 0:
            T[mask_mid] = thr_surrogate.predict(X[mask_mid])
        
        if np.sum(mask_hi_blend) > 0:
            hi_weight = hi_blender.compute(etas[mask_hi_blend])
            T[mask_hi_blend] = thr_surrogate.predict(X[mask_hi_blend])*hi_weight + \
                               (max_thrs[mask_hi_blend] + (etas[mask_hi_blend]-1)*dTdetas[mask_hi_blend])*(1-hi_weight)
        
        T[mask_high] = max_thrs[mask_high] + (etas[mask_high]-1)*dTdetas[mask_high]
        
        return T
    
    def extended_sfc_surrogate(self, sfc_surrogate, X, lo_blender, hi_blender):
        # initialize
        X_zero_eta = deepcopy(X)
        X_one_eta = deepcopy(X)
        X_zero_eta[:,2] = 0
        X_one_eta[:,2] = 1    
        etas = X[:,2]
        mask_low = etas < 0
        mask_lo_blend = np.logical_and(etas >= 0, etas < 0.01)
        mask_mid = np.logical_and(etas >= 0.01, etas < 0.99)
        mask_hi_blend = np.logical_and(etas >= 0.99, etas < 1)
        mask_high = etas >= 1 
        
        etas = np.atleast_2d(etas).T
        sfcs = np.zeros_like(etas)
        
        # compute sfc
        if np.sum(mask_low) > 0:
            sfcs[mask_low] = sfc_surrogate.predict(X_zero_eta[mask_low])
        
        if np.sum(mask_lo_blend) > 0:
            lo_weight = lo_blender.compute(etas[mask_lo_blend])
            sfcs[mask_lo_blend] = sfc_surrogate.predict(X_zero_eta[mask_lo_blend])*lo_weight + \
                               sfc_surrogate.predict(X[mask_lo_blend])*(1-lo_weight)
        
        if np.sum(mask_mid) > 0:
            sfcs[mask_mid] = sfc_surrogate.predict(X[mask_mid])
        
        if np.sum(mask_hi_blend) > 0:
            hi_weight = hi_blender.compute(etas[mask_hi_blend])
            sfcs[mask_hi_blend] = sfc_surrogate.predict(X[mask_hi_blend])*hi_weight + \
                               sfc_surrogate.predict(X_one_eta[mask_hi_blend])*(1-hi_weight)
        
        if np.sum(mask_high) > 0:
            sfcs[mask_high] = sfc_surrogate.predict(X_one_eta[mask_high])
            
        return sfcs    
       
       
       
if __name__ == '__main__':
    
    from SUAVE.Core import Units
   
    alt = np.array([[10000,10000,10000,10000,20000,20000,20000,20000]]).T
    mach = np.array([[.7,.7,.8,.8,.7,.7,.8,.8]]).T
    thr = np.array([[0,1,0,1,0,1,0,1]]).T
    sfc = np.array([[.5,.5,.45,.45,.48,.48,.43,.43]]).T
    thrust = np.array([[0,1000,0,900,0,800,0,700]]).T
   
    my_data = np.hstack([alt,mach,thr,thrust,sfc])
    my_data = None
   
    sur = Propulsor_Surrogate()
    my_data = None
    sur.input_file = 'sfc_hook_set_SI.csv'
    sur.use_extended_surrogate = True
    sur.number_of_engines = 1
    sur.surrogate_type = 'gaussian'
    
    sur.thrust_anchor = 35000*Units.lbf
    sur.sfc_anchor    = 1.65*Units.lb/Units.lbf/Units.hour
    sur.thrust_anchor_conditions = np.array([[15000*Units.ft,0.8,1]])
    sur.sfc_anchor_conditions = np.array([[15000*Units.ft,0.8,1]])
    
    sur.build_surrogate(my_data=my_data)
   
    x = np.atleast_2d(np.linspace(5000,25000))
    y = np.atleast_2d(np.linspace(.7, .8))
    t = np.atleast_2d(np.linspace(0,1))
    x0 = 15000*np.ones_like(x)*Units.ft
    y0 = .8*np.ones_like(x)
    t0 = 1.*np.ones_like(x)
    
    cond = np.hstack([x0.T,y0.T,t.T])
    
    state = Data()
    state.conditions = Data()
    state.conditions.freestream = Data()
    state.conditions.freestream.altitude    = x0.T
    state.conditions.freestream.mach_number = y0.T
    state.conditions.propulsion = Data()
    state.conditions.propulsion.throttle    = t.T
   
    import matplotlib.pyplot as plt
    
    res = sur.evaluate_thrust(state)
    sfc = res.tsfc
    thr = res.thrust_scalar_value
   
    fig = plt.figure()
    ax = plt.gca()
    ax.plot(thr/Units.lbf, sfc*4.4*2.2*3600)
    
    plt.show()
   
    aa = 0