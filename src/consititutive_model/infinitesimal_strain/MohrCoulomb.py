import taichi as ti

from src.consititutive_model.MaterialKernel import *
from src.utils.constants import DELTA2D, DELTA, MAXITS, FTOL, PI
from src.utils.MatrixFunction import matrix_form
from src.utils.TypeDefination import mat3x3
from src.utils.VectorFunction import voigt_form, voigt_tensor_dot


@ti.dataclass
class ULStateVariable:
    epstrain: float
    estress: float

    @ti.func
    def _initialize_vars(self, np, particle, matProps):
        stress = particle[np].stress
        materialID = int(particle[np].materialID)
        self.estress = VonMisesStress(stress)

    @ti.func
    def _update_vars(self, stress, epstrain):
        self.estress = VonMisesStress(stress)
        self.epstrain = epstrain


@ti.dataclass
class TLStateVariable:
    epstrain: float
    estress: float
    deformation_gradient: mat3x3
    stress: mat3x3

    @ti.func
    def _initialize_vars(self, np, particle, matProps):
        stress = particle[np].stress
        materialID = int(particle[np].materialID)
        self.estress = VonMisesStress(stress)
        self.deformation_gradient = DELTA
        self.stress = matrix_form(stress)

    @ti.func
    def _update_deformation_gradient(self, deformation_gradient_rate, dt):
        self.deformation_gradient += deformation_gradient_rate * dt[None]

    @ti.func
    def _update_vars(self, stress, epstrain):
        self.estress = VonMisesStress(stress)
        self.epstrain = epstrain


@ti.dataclass
class MohrCoulombModel:
    density: float
    young: float
    possion: float
    shear: float
    bulk: float
    c_peak: float
    fai_peak: float
    psi_peak: float
    c_residual: float
    fai_residual: float
    psi_residual: float
    pdstrain_peak: float
    pdstrain_residual: float
    tensile: float

    def add_material(self, density, young, possion, c_peak, fai_peak, psi_peak, c_residual, fai_residual, psi_residual, pdstrain_peak, pdstrain_residual, tensile):
        self.density = density
        self.young = young
        self.possion = possion

        self.shear = 0.5 * self.young / (1. + self.possion)
        self.bulk = self.young / (3. * (1 - 2. * self.possion))
        self.c_peak = c_peak
        self.fai_peak = fai_peak
        self.psi_peak = psi_peak
        self.c_residual = c_residual
        self.fai_residual = fai_residual
        self.psi_residual = psi_residual
        self.pdstrain_peak = pdstrain_peak
        self.pdstrain_residual = pdstrain_residual

        if self.fai_peak == 0:
            self.tensile = 0.
        else:
            self.tensile = ti.min(tensile, self.c_peak / ti.tan(self.fai_peak))

    def add_contact_parameter(self, friction, kn, kt):
        self.friction = friction
        self.kn = kn
        self.kt = kt

    def print_message(self, materialID):
        print(" Constitutive Model Information ".center(71, '-'))
        if self.fai_peak > Threshold:
            print('Constitutive model: Mohr-Coulomb Model')
        else:
            print('Constitutive model: Tresca Model')
        print("Model ID: ", materialID)
        print('Density: ', self.density)
        print('Young Modulus: ', self.young)
        print('Possion Ratio: ', self.possion)
        print('Peak Cohesion Coefficient = ', self.c_peak)
        print('Peak Internal Friction (in radian) = ', self.fai_peak)
        print('Peak Dilatation (in radian) = ', self.psi_peak)
        print('Residual Cohesion Coefficient = ', self.c_residual)
        print('Residual Internal Friction (in radian) = ', self.fai_residual)
        print('Residual Dilatation (in radian) = ', self.psi_residual)
        print('Peak Plastic Deviartoric Strain = ', self.pdstrain_peak)
        print('Residual Plastic Deviartoric Strain = ', self.pdstrain_residual)
        print('Tensile = ', self.tensile, '\n')

    @ti.func
    def _get_sound_speed(self):
        sound_speed = 0.
        if self.density > 0.:
            sound_speed = ti.sqrt(self.young * (1 - self.possion) / (1 + self.possion) / (1 - 2 * self.possion) / self.density)
        return sound_speed
    
    @ti.func
    def update_particle_volume(self, np, velocity_gradient, stateVars, dt):
        return (DELTA + velocity_gradient * dt[None]).determinant()
    
    @ti.func
    def update_particle_volume_2D(self, np, velocity_gradient, stateVars, dt):
        return (DELTA2D + velocity_gradient * dt[None]).determinant()
    
    @ti.func
    def update_particle_volume_bbar(self, np, strain_rate, stateVars, dt):
        return 1. + dt[None] * (strain_rate[0] + strain_rate[1] + strain_rate[2])
    
    @ti.func
    def PK2CauchyStress(self, np, stateVars):
        inv_j = 1. / stateVars[np].deformation_gradient.determinant()
        return voigt_form(stateVars[np].stress @ stateVars[np].deformation_gradient.transpose() * inv_j)

    @ti.func
    def Cauchy2PKStress(self, np, stateVars, stress):
        j = stateVars[np].deformation_gradient.determinant()
        return matrix_form(stress) @ stateVars[np].deformation_gradient.inverse().transpose() * j
    
    # ==================================================== Mohr-Coulomb Model ==================================================== #
    @ti.func
    def get_epsilon(self, stress):
        return ti.sqrt(3) * MeanStress(stress)
    
    @ti.func
    def get_sqrt2J2(self, stress):
        return ti.sqrt(2 * ComputeInvariantJ2(stress))
    
    @ti.func
    def get_lode(self, stress):
        return ComputeLodeAngle(stress)

    @ti.func
    def ComputeStressInvariant(self, stress):
        return self.get_epsilon(stress), self.get_sqrt2J2(stress), self.get_lode(stress)
    
    @ti.func
    def ComputeShearFunction(self, epsilon, sqrt2J2, lode, fai, cohesion):
        cos_fai, tan_fai = ti.cos(fai), ti.tan(fai)
        yield_shear = ti.sqrt(1.5) * sqrt2J2 * (ti.sin(lode + PI/3.) / (ti.sqrt(3.) * cos_fai) + \
                      ti.cos(lode + PI/3.) * tan_fai / 3.) + epsilon * ti.sqrt(1./3.) * tan_fai - cohesion
        return yield_shear
    
    @ti.func
    def ComputeTensileFunction(self, epsilon, sqrt2J2, lode):
        tensile = self.tensile
        cos_lode = ti.cos(lode)
        yield_tensile = ti.sqrt(2./3.) * cos_lode * sqrt2J2 + epsilon * ti.sqrt(1./3.) - tensile
        return yield_tensile
    
    @ti.func
    def ComputeYieldFunction(self, stress, fai, cohesion):
        epsilon, sqrt2J2, lode = self.ComputeStressInvariant(stress)
        yield_shear = self.ComputeShearFunction(epsilon, sqrt2J2, lode, fai, cohesion)
        yield_tensile = self.ComputeTensileFunction(epsilon, sqrt2J2, lode)
        return yield_shear, yield_tensile

    @ti.func
    def ComputeYieldState(self, stress, fai, cohesion, tensile):
        tolerance = -1e-8
        sin_fai = ti.sin(fai)
        epsilon, sqrt2J2, lode = self.ComputeStressInvariant(stress)
        yield_shear, yield_tensile = self.ComputeYieldFunction(stress, fai, cohesion)

        yield_state = 0
        if yield_tensile > tolerance and yield_shear > tolerance:
            n_fai = (1. + sin_fai) / (1. - sin_fai)
            sigma_p = tensile * n_fai - 2. * cohesion * ti.sqrt(n_fai)
            alpha_p = ti.sqrt(1. + n_fai * n_fai) + n_fai
            h = yield_tensile + alpha_p * (ti.sqrt(2./3.) * ti.cos(lode - 4.*PI/3.) * sqrt2J2 + epsilon * ti.sqrt(1./3.) - sigma_p)
            if h > Threshold:
                yield_state = 2
            else:
                yield_state = 1
        if yield_tensile < tolerance and yield_shear > tolerance:
            yield_state = 1
        if yield_tensile > tolerance and yield_shear < tolerance:
            yield_state = 2

        f_function = 0.
        if yield_state == 1:
            f_function = yield_shear
        elif yield_state == 2:
            f_function = yield_tensile
        return yield_state, f_function
    
    @ti.func
    def ComputeDfDsigma(self, yield_state, stress, fai):
        sqrt2J2 = self.get_sqrt2J2(stress)
        lode = self.get_lode(stress)

        df_depsilon, df_dsqrt2J2, df_dlode = 0., 0., 0.
        if yield_state == 2:
            sin_lode, cos_lode = ti.sin(lode), ti.cos(lode)
            df_depsilon = ti.sqrt(1./3.)
            df_dsqrt2J2 = ti.sqrt(2./3.) * cos_lode
            df_dlode = -ti.sqrt(2./3.) * sqrt2J2 * sin_lode
        else:
            sin_lode_PI_3, cos_lode_PI_3 = ti.sin(lode + PI/3.), ti.cos(lode + PI/3.)
            cos_fai, tan_fai = ti.cos(fai), ti.tan(fai)
            df_depsilon = tan_fai * ti.sqrt(1./3.)
            df_dsqrt2J2 = ti.sqrt(1.5) * (sin_lode_PI_3 / (ti.sqrt(3.) * cos_fai) + cos_lode_PI_3 * tan_fai / 3.)
            df_dlode = ti.sqrt(1.5) * sqrt2J2 * (cos_lode_PI_3 / (ti.sqrt(3.) * cos_fai) - sin_lode_PI_3 * tan_fai / 3.)
        
        depsilon_dsigma = DpDsigma() * ti.sqrt(3.)
        dsqrt2J2_dsigma = DqDsigma(stress) * ti.sqrt(2./3.)
        dlode_dsigma = DlodeDsigma(stress)
        df_dsigma = df_depsilon * depsilon_dsigma + df_dsqrt2J2 * dsqrt2J2_dsigma + df_dlode * dlode_dsigma
        return df_dsigma
    
    @ti.func
    def ComputeDfDstateV(self, stress, fai, pdstrain):
        sqrt2J2 = self.get_sqrt2J2(stress)
        lode = self.get_lode(stress)

        dfdpdstrain = 0.
        sin_fai, cos_fai = ti.sin(fai), ti.cos(fai)
        c_peak, c_residual = self.c_peak, self.c_residual
        fai_peak, fai_residual = self.fai_peak, self.fai_residual
        pdstrain_peak, pdstrain_residual = self.pdstrain_peak, self.pdstrain_residual
        if pdstrain > self.pdstrain_peak and pdstrain < pdstrain_residual:
            dfai_dpstrain = (fai_residual - fai_peak) / (pdstrain_residual - pdstrain_peak)
            dc_dpstrain = (c_residual - c_peak) / (pdstrain_residual - pdstrain_peak)
            df_dfai = ti.sqrt(1.5) * sqrt2J2 * (sin_fai * ti.sin(lode + PI / 3.) / (ti.sqrt(3.) * cos_fai * cos_fai) + ti.cos(lode + PI / 3.) / (3. * cos_fai * cos_fai)) + MeanStress(stress) / (cos_fai * cos_fai)
            df_dc = -1
            dfdpdstrain = df_dfai * dfai_dpstrain + df_dc * dc_dpstrain
        return dfdpdstrain
    
    @ti.func
    def ComputeRFunction(self):
        pass

    @ti.func
    def ComputePlasticModulus(self, dgdp, dgdq, stress, fai, epstrain):
        dfdpdstrain = self.ComputeDfDstateV(stress, fai, epstrain)
        return dfdpdstrain * dgdq
    
    @ti.func
    def ComputeDgDsigma(self, yield_state, stress, fai, psi, cohesion, tensile):
        sqrt2J2 = self.get_sqrt2J2(stress)
        lode = self.get_lode(stress)

        xi, xit = 0.1, 0.1
        sin_lode, cos_lode = ti.sin(lode), ti.cos(lode)
        sin_fai, cos_fai, tan_psi = ti.sin(fai), ti.cos(fai), ti.tan(psi)

        depsilon_dsigma = DpDsigma() * ti.sqrt(3.)
        dsqrt2J2_dsigma = DqDsigma(stress) * ti.sqrt(2./3.)
        dlode_dsigma = DlodeDsigma(stress)
        
        dg_dp, dg_dq = 0., 0.
        dg_depsilon, dg_dsqrt2J2, dg_dlode = 0., 0., 0.
        if yield_state == 2:
            et_value = 0.6
            sqpart = 4. * (1 - et_value * et_value) * cos_lode * cos_lode + 5. * et_value * et_value - 4. * et_value
            if sqpart < Threshold: sqpart = 1e-5
            rt_den = 2. * (1 - et_value * et_value) * cos_lode + (2. * et_value - 1) * ti.sqrt(sqpart)
            rt_num = 4. * (1 - et_value * et_value) * cos_lode * cos_lode + (2. * et_value - 1) * (2. * et_value - 1)
            if ti.abs(rt_den) < Threshold: rt_den = 1e-5
            rt = rt_num / (3. * rt_den)
            temp_den = ti.sqrt(xit * xit * tensile * tensile + 1.5 * rt * rt * sqrt2J2 * sqrt2J2)
            if temp_den < Threshold: temp_den = Threshold
            dg_dp = 1.
            dg_dq = ti.sqrt(1.5) * sqrt2J2 * rt * rt / temp_den
            dp_drt = 1.5 * sqrt2J2 * sqrt2J2 * rt / temp_den
            drtden_dlode = -2. * (1 - et_value * et_value) * sin_lode - (2. * et_value - 1) * 4. * (1 - et_value * et_value) * cos_lode * \
                            sin_lode / ti.sqrt(4. * (1 - et_value * et_value) * cos_lode * cos_lode + 5. * et_value * et_value - 4. * et_value)
            drtnum_dlode = -8. * (1 - et_value * et_value) * cos_lode * sin_lode
            drt_dlode = (drtnum_dlode * rt_den - drtden_dlode * rt_num) / (3. * rt_den * rt_den)
            dg_dlode = dp_drt * drt_dlode 
        else:
            r_mc = (3. - sin_fai) / (6. * cos_fai)
            e_val = (3. - sin_fai) / (3. + sin_fai)
            e_val = clamp(0.5 + 1e-10, 1., e_val)
            sqpart = 4. * (1 - e_val * e_val) * cos_lode * cos_lode + 5 * e_val * e_val - 4 * e_val
            if sqpart < Threshold: sqpart = 1e-10
            m = 2. * (1 - e_val * e_val) * cos_lode + (2. * e_val - 1) * ti.sqrt(sqpart)
            if ti.abs(m) < Threshold: m = 1e-10
            l = 4. * (1 - e_val * e_val) * cos_lode * cos_lode + (2. * e_val - 1) * (2. * e_val - 1)
            r_mw = (l / m) * r_mc
            omega = (xi * cohesion * tan_psi) * (xi * cohesion * tan_psi) + (r_mw * ti.sqrt(1.5) * sqrt2J2) * (r_mw * ti.sqrt(1.5) * sqrt2J2)
            if omega < Threshold: omega = 1e-10
            dl_dlode = -8. * (1. - e_val * e_val) * cos_lode * sin_lode
            dm_dlode = -2. * (1. - e_val * e_val) * sin_lode + (0.5 * (2. * e_val - 1.) * dl_dlode) / ti.sqrt(sqpart)
            drmw_dlode = ((m * dl_dlode) - (l * dm_dlode)) / (m * m)
            dg_dp = tan_psi
            dg_dq = sqrt2J2 * r_mw * r_mw / (2. * ti.sqrt(omega)) * ti.sqrt(6.)
            dg_dlode = (3. * sqrt2J2 * sqrt2J2 * r_mw * r_mc * drmw_dlode) / (2. * ti.sqrt(omega))
        
        dg_depsilon = dg_dp / ti.sqrt(3.)
        dg_dsqrt2J2 = dg_dq * ti.sqrt(1.5)
        dg_dsigma = (dg_depsilon * depsilon_dsigma) + (dg_dsqrt2J2 * dsqrt2J2_dsigma) + (dg_dlode * dlode_dsigma)
        return dg_dp, dg_dq, dg_dsigma

    @ti.func
    def ComputeElasticStress(self, dstrain, stress):
        return stress + self.ComputeElasticStressIncrement(dstrain, stress)
    
    @ti.func
    def ComputeElasticStressIncrement(self, dstrain, stress):
        bulk_modulus = self.bulk
        shear_modulus = self.shear

        # !-- trial elastic stresses ----!
        dstress = ElasticTensorMultiplyVector(dstrain, bulk_modulus, shear_modulus)
        return dstress

    @ti.func
    def ComputeStress2D(self, np, previous_stress, velocity_gradient, stateVars, dt):  
        de = calculate_strain_increment2D(velocity_gradient, dt)
        dw = calculate_vorticity_increment2D(velocity_gradient, dt)
        return self.core(np, previous_stress, de, dw, stateVars)

    @ti.func
    def ComputeStress(self, np, previous_stress, velocity_gradient, stateVars, dt):  
        de = calculate_strain_increment(velocity_gradient, dt)
        dw = calculate_vorticity_increment(velocity_gradient, dt)
        return self.core(np, previous_stress, de, dw, stateVars)
    
    @ti.func
    def soften(self, pdstrain):
        pdstrain_peak, pdstrain_residual = self.pdstrain_peak, self.pdstrain_residual
        c_peak, c_residual = self.c_peak, self.c_residual
        fai_peak, fai_residual = self.fai_peak, self.fai_residual
        psi_peak, psi_residual = self.psi_peak, self.psi_residual
        fai, psi, cohesion, tensile = fai_peak, psi_peak, c_peak, self.tensile
        if pdstrain > pdstrain_peak:
            if pdstrain < pdstrain_residual:
                fai = fai_residual + (fai_peak - fai_residual) * (pdstrain - pdstrain_residual) / (pdstrain_peak - pdstrain_residual)
                psi = psi_residual + (psi_peak - psi_residual) * (pdstrain - pdstrain_residual) / (pdstrain_peak - pdstrain_residual)
                cohesion = c_residual + (c_peak - c_residual) * (pdstrain - pdstrain_residual) / (pdstrain_peak - pdstrain_residual)
            else:
                fai = fai_residual
                psi = psi_residual
                cohesion = c_residual
            apex = cohesion / ti.max(ti.tan(fai), Threshold)
            if tensile > apex: tensile = ti.max(apex, Threshold)
        return fai, psi, cohesion, tensile
    
    @ti.func
    def core(self, np, previous_stress, de, dw, stateVars): 
        bulk_modulus = self.bulk
        shear_modulus = self.shear
        epstrain = stateVars[np].epstrain
        fai, psi, cohesion, tensile = self.soften(epstrain)

        # !-- trial elastic stresses ----!
        stress = previous_stress
        sigrot = Sigrot(stress, dw)
        dstress = ElasticTensorMultiplyVector(de, bulk_modulus, shear_modulus)
        trial_stress = stress + dstress 

        # !-- compute trial stress invariants ----!
        pdstrain = 0.
        updated_stress = trial_stress
        yield_state_trial, f_function_trial = self.ComputeYieldState(trial_stress, fai, cohesion, tensile)
        
        # !-- implicit return mapping ----!
        if yield_state_trial > 0:
            Tolerance = 1e-1

            dfdsigma_trial = self.ComputeDfDsigma(yield_state_trial, trial_stress, fai)
            dgdp_trial, dgdq_trial, dgdsigma_trial = self.ComputeDgDsigma(yield_state_trial, trial_stress, fai, psi, cohesion, tensile)
            softening_trial = self.ComputePlasticModulus(dgdp_trial, dgdq_trial, trial_stress, fai, epstrain)
            temp_matrix = ElasticTensorMultiplyVector(dfdsigma_trial, bulk_modulus, shear_modulus)
            den = (temp_matrix).dot(dgdsigma_trial) - softening_trial
            lambda_trial = ti.max(0., f_function_trial / den if ti.abs(den) > Tolerance else 0.)
            
            yield_state, f_function = self.ComputeYieldState(stress, fai, cohesion, tensile)
            dfdsigma = self.ComputeDfDsigma(yield_state, stress, fai)
            dgdp, dgdq, dgdsigma = self.ComputeDgDsigma(yield_state, stress, fai, psi, cohesion, tensile)
            softening = self.ComputePlasticModulus(dgdp, dgdq, stress, fai, epstrain)
            temp_matrix = ElasticTensorMultiplyVector(dfdsigma, bulk_modulus, shear_modulus)
            den = (temp_matrix).dot(dgdsigma) - softening
            _lambda = ti.max(0., temp_matrix.dot(de) / den if ti.abs(den) > Tolerance else 0.)

            pdstrain = 0.
            if ti.abs(f_function) > Tolerance or yield_state == 0:
                temp_matrix = ElasticTensorMultiplyVector(dgdsigma, bulk_modulus, shear_modulus)
                updated_stress -= _lambda * temp_matrix
                pdstrain = _lambda * dgdq
            else:
                temp_matrix = ElasticTensorMultiplyVector(dgdsigma_trial, bulk_modulus, shear_modulus)
                updated_stress -= lambda_trial * temp_matrix
                pdstrain = lambda_trial * dgdq_trial

            yield_state, f_function = self.ComputeYieldState(updated_stress, fai, cohesion, tensile)
            if ti.abs(f_function) > FTOL:
                updated_stress, pdstrain = self.DriftCorrect(yield_state, f_function, updated_stress, pdstrain, fai, psi, cohesion, tensile, epstrain)
            
        updated_stress += sigrot
        stateVars[np].estress = VonMisesStress(updated_stress)
        stateVars[np].epstrain += pdstrain
        return updated_stress
    
    @ti.func
    def ConsistentCorrection(self, yield_state, f_function, stress, pdstrain, fai, psi, cohesion, tensile, epstrain):
        bulk_modulus, shear_modulus = self.bulk, self.shear
        dfdsigma = self.ComputeDfDsigma(yield_state, stress, fai)
        dgdp, dgdq, dgdsigma = self.ComputeDgDsigma(yield_state, stress, fai, psi, cohesion, tensile)
        softening_trial = self.ComputePlasticModulus(dgdp, dgdq, stress, fai, epstrain)
        temp_matrix = ElasticTensorMultiplyVector(dgdsigma, bulk_modulus, shear_modulus)
        lambda_trial = f_function / ((temp_matrix).dot(dfdsigma) - softening_trial)
        dstress = lambda_trial * temp_matrix
        dpdstrain = lambda_trial * dgdq
        return stress - dstress, pdstrain + dpdstrain

    @ti.func
    def NormalCorrection(self, yield_state, f_function, stress, fai):
        dfdsigma = self.ComputeDfDsigma(yield_state, stress, fai)
        dfdsigmadfdsigma = voigt_tensor_dot(dfdsigma, dfdsigma)
        abeta = 1. / dfdsigmadfdsigma if ti.abs(dfdsigmadfdsigma) > Threshold else 0.
        dlambda = f_function * abeta
        dstress = dlambda * dfdsigma
        return stress - dstress

    @ti.func
    def DriftCorrect(self, yield_state, f_function, stress, pdstrain, fai, psi, cohesion, tensile, epstrain):
        for _ in range(MAXITS):
            stress_new, pdstrain_new = self.ConsistentCorrection(yield_state, f_function, stress, pdstrain, fai, psi, cohesion, tensile, epstrain)
            yield_state_new, f_function_new = self.ComputeYieldState(stress_new, fai, cohesion, tensile)

            if ti.abs(f_function_new) > ti.abs(f_function):
                stress_new = self.NormalCorrection(yield_state, f_function, stress, fai)
                yield_state_new, f_function_new = self.ComputeYieldState(stress_new, fai, cohesion, tensile)
                pdstrain_new = pdstrain

            stress = stress_new
            pdstrain = pdstrain_new
            yield_state = yield_state_new
            f_function = f_function_new
            if ti.abs(f_function_new) <= FTOL:
                break
        return stress, pdstrain
    
    @ti.func
    def compute_elastic_tensor(self, np, current_stress, stateVars):
        return ComputeElasticStiffnessTensor(self.bulk, self.shear)

    @ti.func
    def compute_stiffness_tensor(self, np, current_stress, stateVars):
        stiffness_matrix = self.compute_elastic_tensor(np, current_stress, stateVars)
        yield_state, f_function = self.ComputeYieldState(current_stress, stateVars[np])
        fai, psi, cohesion, tensile = self.soften(np, stateVars)
        if yield_state > 0:
            bulk_modulus = self.bulk
            shear_modulus = self.shear

            dfdsigma = self.ComputeDfDsigma(yield_state, current_stress, fai)
            dgdp, dgdq, dgdsigma = self.ComputeDgDsigma(yield_state, current_stress, fai, psi, cohesion, tensile)
            softening = self.ComputePlasticModulus(dgdp, dgdq, current_stress, fai, stateVars[np].epstrain)
            tempMatf = ElasticTensorMultiplyVector(dfdsigma, bulk_modulus, shear_modulus)
            tempMatg = ElasticTensorMultiplyVector(dgdsigma, bulk_modulus, shear_modulus)
            dfdsigmaDedgdsigma = voigt_tensor_dot(dgdsigma, tempMatf)
            stiffness_matrix -= 1. / (dfdsigmaDedgdsigma - softening) * (tempMatg.outer_product(tempMatf))
        return stiffness_matrix

    @ti.func
    def ComputePKStress(self, np, velocity_gradient, stateVars, dt):  
        previous_stress = self.PK2CauchyStress(np, stateVars)
        cauchy_stress = self.ComputeStress(np, previous_stress, velocity_gradient, stateVars, dt)
        PKstress = self.Cauchy2PKStress(np, stateVars, cauchy_stress)
        stateVars[np].stress = PKstress
        return PKstress


@ti.kernel
def kernel_reload_state_variables(estress: ti.types.ndarray(), epstrain: ti.types.ndarray(), state_vars: ti.template()):
    for np in range(estress.shape[0]):
        state_vars[np].estress = estress[np]
        state_vars[np].epstrain = epstrain[np]