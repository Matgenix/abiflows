# coding: utf-8
"""
Firework workflows
"""
from __future__ import print_function, division, unicode_literals

from fireworks.core.launchpad import LaunchPad
from fireworks.core.firework import Firework, Workflow

import abc
import six
import os
import logging
import sys

from abiflows.fireworks.tasks.abinit_tasks import AbiFireTask, ScfFWTask, RelaxFWTask, NscfFWTask, HybridFWTask, RelaxDilatmxFWTask, GeneratePhononFlowFWTask
from abiflows.fireworks.tasks.abinit_tasks import AnaDdbTask, StrainPertTask, DdkTask, MergeDdbTask
from abiflows.fireworks.tasks.utility_tasks import FinalCleanUpTask, DatabaseInsertTask
from abiflows.fireworks.utils.fw_utils import append_fw_to_wf, get_short_single_core_spec
from abipy.abio.factories import ion_ioncell_relax_input, scf_input
from abipy.abio.factories import HybridOneShotFromGsFactory, ScfFactory, IoncellRelaxFromGsFactory, PhononsFromGsFactory, ScfForPhononsFactory
from abipy.abio.inputs import AbinitInput, AnaddbInput
from monty.serialization import loadfn

# logging.basicConfig()
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))

@six.add_metaclass(abc.ABCMeta)
class AbstractFWWorkflow(Workflow):
    """
    Abstract Workflow class.
    """

    def add_to_db(self, lpad=None):
        if not lpad:
            lpad = LaunchPad.auto_load()
        return lpad.add_wf(self.wf)

    def append_fw(self, fw, short_single_spec=False):
        if short_single_spec:
            fw.spec.update(self.set_short_single_core_to_spec())
        append_fw_to_wf(fw, self.wf)

    @staticmethod
    def set_short_single_core_to_spec(spec=None):
        if spec is None:
                spec = {}
        spec = dict(spec)

        qadapter_spec = get_short_single_core_spec()
        spec['mpi_ncpus'] = 1
        spec['_queueadapter'] = qadapter_spec
        return spec

    def add_final_cleanup(self, out_exts=None):
        if out_exts is None:
            out_exts = ["WFK"]
        spec = self.set_short_single_core_to_spec()
        # high priority
        #TODO improve the handling of the priorities
        spec['_priority'] = 100
        cleanup_fw = Firework(FinalCleanUpTask(out_exts=out_exts), spec=spec,
                              name=(self.wf.name+"_cleanup")[:15])

        append_fw_to_wf(cleanup_fw, self.wf)

    def add_db_insert_and_cleanup(self, mongo_database, out_exts=None, insertion_data=None, criteria=None):
        if out_exts is None:
            out_exts = ["WFK"]
        if insertion_data is None:
            insertion_data = {'structure': 'get_final_structure_and_history'}
        spec = self.set_short_single_core_to_spec()
        spec['mongo_database'] = mongo_database.as_dict()
        insert_and_cleanup_fw = Firework([DatabaseInsertTask(insertion_data=insertion_data, criteria=criteria),
                                          FinalCleanUpTask(out_exts=out_exts)],
                                         spec=spec,
                                         name=(self.wf.name+"_insclnup")[:15])

        append_fw_to_wf(insert_and_cleanup_fw, self.wf)

    def add_anaddb_task(self, structure):
        spec = self.set_short_single_core_to_spec()
        anaddb_task = AnaDdbTask(AnaddbInput.piezo_elastic(structure))
        anaddb_fw = Firework([anaddb_task],
                             spec=spec,
                             name='anaddb')
        append_fw_to_wf(anaddb_fw, self.wf)

    def add_metadata(self, structure=None, additional_metadata=None):
        if additional_metadata is None:
            additional_metadata = {}
        metadata = dict(wf_type = self.__class__.__name__)
        if structure:
            composition = structure.composition
            metadata['nsites'] = len(structure)
            metadata['elements'] = [el.symbol for el in composition.elements]
            metadata['reduced_formula'] = composition.reduced_formula

        metadata.update(additional_metadata)

        self.wf.metadata.update(metadata)

    def get_reduced_formula(self, input):
        structure = None
        try:
            if isinstance(input, AbinitInput):
                structure = input.structure
            elif 'structure' in input.kwargs:
                structure = input.kwargs['structure']
            else:
                structure = input.args[0]
        except Exception as e:
            logger.warning("Couldn't get the structure from the input: {} {}".format(e.__class__.__name__, e.message))

        return structure.composition.reduced_formula if structure else ""


class InputFWWorkflow(AbstractFWWorkflow):
    def __init__(self, abiinput, task_type=AbiFireTask, autoparal=False, spec=None, initialization_info=None):
        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        abitask = task_type(abiinput, is_autoparal=autoparal)

        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)

        self.fw = Firework(abitask, spec=spec)

        self.wf = Workflow([self.fw])
        # Workflow.__init__([self.fw])


class ScfFWWorkflow(AbstractFWWorkflow):
    def __init__(self, abiinput, autoparal=False, spec=None, initialization_info=None):
        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        abitask = ScfFWTask(abiinput, is_autoparal=autoparal)

        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)

        self.scf_fw = Firework(abitask, spec=spec)

        self.wf = Workflow([self.scf_fw])

    @classmethod
    def from_factory(cls, structure, pseudos, kppa=None, ecut=None, pawecutdg=None, nband=None, accuracy="normal",
                     spin_mode="polarized", smearing="fermi_dirac:0.1 eV", charge=0.0, scf_algorithm=None,
                     shift_mode="Monkhorst-Pack", extra_abivars=None, decorators=None, autoparal=False, spec=None):
        if extra_abivars is None:
                extra_abivars = {}
        if decorators is None:
                decorators = []
        if spec is None:
                spec = {}
        abiinput = scf_input(structure, pseudos, kppa=kppa, ecut=ecut, pawecutdg=pawecutdg, nband=nband,
                             accuracy=accuracy, spin_mode=spin_mode, smearing=smearing, charge=charge,
                             scf_algorithm=scf_algorithm, shift_mode=shift_mode)
        abiinput.set_vars(extra_abivars)
        for d in decorators:
            d(abiinput)

        return cls(abiinput, autoparal=autoparal, spec=spec)


class RelaxFWWorkflow(AbstractFWWorkflow):
    workflow_class = 'RelaxFWWorkflow'
    workflow_module = 'abiflows.fireworks.workflows.abinit_workflows'

    def __init__(self, ion_input, ioncell_input, autoparal=False, spec=None, initialization_info=None, target_dilatmx=None):
        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        start_task_index = 1
        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)
            start_task_index = 'autoparal'

        spec['wf_task_index'] = 'ion_' + str(start_task_index)
        ion_task = RelaxFWTask(ion_input, is_autoparal=autoparal)
        self.ion_fw = Firework(ion_task, spec=spec)

        spec['wf_task_index'] = 'ioncell_' + str(start_task_index)
        if target_dilatmx:
            ioncell_task = RelaxDilatmxFWTask(ioncell_input, is_autoparal=autoparal, target_dilatmx=target_dilatmx)
        else:
            ioncell_task = RelaxFWTask(ioncell_input, is_autoparal=autoparal)

        self.ioncell_fw = Firework(ioncell_task, spec=spec)

        self.wf = Workflow([self.ion_fw, self.ioncell_fw], {self.ion_fw: [self.ioncell_fw]},
                           metadata={'workflow_class': self.workflow_class,
                                     'workflow_module': self.workflow_module})

    @classmethod
    def get_final_structure_and_history(cls, wf):
        assert wf.metadata['workflow_class'] == cls.workflow_class
        assert wf.metadata['workflow_module'] == cls.workflow_module
        ioncell = -1
        final_fw_id = None
        for fw_id, fw in wf.id_fw.items():
            if 'wf_task_index' in fw.spec and fw.spec['wf_task_index'][:8] == 'ioncell_':
                try:
                    this_ioncell =  int(fw.spec['wf_task_index'].split('_')[-1])
                except ValueError:
                    # skip if the index is not an int
                    continue
                if this_ioncell > ioncell:
                    ioncell = this_ioncell
                    final_fw_id = fw_id
        if final_fw_id is None:
            raise RuntimeError('Final strucure not found ...')
        myfw = wf.id_fw[final_fw_id]
        #TODO add a check on the state of the launches
        last_launch = (myfw.archived_launches + myfw.launches)[-1]
        #TODO add a cycle to find the instance of AbiFireTask?
        myfw.tasks[-1].set_workdir(workdir=last_launch.launch_dir)
        structure = myfw.tasks[-1].get_final_structure()
        history = loadfn(os.path.join(last_launch.launch_dir, 'history.json'))

        return {'structure': structure.as_dict(), 'history': history}

    @classmethod
    def get_runtime_secs(cls, wf):
        assert wf.metadata['workflow_class'] == cls.workflow_class
        assert wf.metadata['workflow_module'] == cls.workflow_module
        time_secs = 0.0
        for fw_id, fw in wf.id_fw.items():
            if 'wf_task_index' in fw.spec:
                if fw.spec['wf_task_index'][-9:] == 'autoparal':
                    time_secs += fw.launches[-1].runtime_secs
                elif fw.spec['wf_task_index'][:4] == 'ion_':
                    time_secs += fw.launches[-1].runtime_secs * fw.spec['mpi_ncpus']
                elif fw.spec['wf_task_index'][:8] == 'ioncell_':
                    time_secs += fw.launches[-1].runtime_secs * fw.spec['mpi_ncpus']
        return time_secs

    @classmethod
    def from_factory(cls, structure, pseudos, kppa=None, nband=None, ecut=None, pawecutdg=None, accuracy="normal",
                     spin_mode="polarized", smearing="fermi_dirac:0.1 eV", charge=0.0, scf_algorithm=None,
                     extra_abivars=None, decorators=None, autoparal=False, spec=None, initialization_info=None,
                     target_dilatmx=None):

        if extra_abivars is None:
                extra_abivars = {}
        if decorators is None:
                decorators = []
        if spec is None:
                spec = {}
        if initialization_info is None:
                initialization_info = {}
        ion_input = ion_ioncell_relax_input(structure=structure, pseudos=pseudos, kppa=kppa, nband=nband, ecut=ecut,
                                            pawecutdg=pawecutdg, accuracy=accuracy, spin_mode=spin_mode,
                                            smearing=smearing, charge=charge, scf_algorithm=scf_algorithm)[0]

        ion_input.set_vars(**extra_abivars)
        for d in decorators:
            ion_input = d(ion_input)

        ioncell_fact = IoncellRelaxFromGsFactory(accuracy=accuracy, extra_abivars=extra_abivars, decorators=decorators)

        return cls(ion_input, ioncell_fact, autoparal=autoparal, spec=spec, initialization_info=initialization_info,
                   target_dilatmx=target_dilatmx)



class NscfFWWorkflow(AbstractFWWorkflow):
    def __init__(self, scf_input, nscf_input, autoparal=False, spec=None, initialization_info=None):

        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)

        ion_task = ScfFWTask(scf_input, is_autoparal=autoparal)
        self.ion_fw = Firework(ion_task, spec=spec)

        ioncell_task = NscfFWTask(nscf_input, deps={ion_task.task_type: 'DEN'}, is_autoparal=autoparal)
        self.ioncell_fw = Firework(ioncell_task, spec=spec)

        self.wf = Workflow([self.ion_fw, self.ioncell_fw], {self.ion_fw: [self.ioncell_fw]})


class HybridOneShotFWWorkflow(AbstractFWWorkflow):
    def __init__(self, scf_inp, hybrid_input, autoparal=False, spec=None, initialization_info=None):
        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        rf = self.get_reduced_formula(scf_inp)

        scf_task = ScfFWTask(scf_inp, is_autoparal=autoparal)

        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)

        self.scf_fw = Firework(scf_task, spec=spec, name=rf+"_"+scf_task.task_type)

        hybrid_task = HybridFWTask(hybrid_input, is_autoparal=autoparal, deps=["WFK"])

        self.hybrid_fw = Firework(hybrid_task, spec=spec, name=rf+"_"+hybrid_task.task_type)

        self.wf = Workflow([self.scf_fw, self.hybrid_fw], {self.scf_fw: self.hybrid_fw})

    @classmethod
    def from_factory(cls, structure, pseudos, kppa=None, ecut=None, pawecutdg=None, nband=None, accuracy="normal",
                     spin_mode="polarized", smearing="fermi_dirac:0.1 eV", charge=0.0, scf_algorithm=None,
                     shift_mode="Monkhorst-Pack", hybrid_functional="hse06", ecutsigx=None, gw_qprange=1,
                     extra_abivars=None, decorators=None, autoparal=False, spec=None, initialization_info=None):

        if extra_abivars is None:
                extra_abivars = {}
        if decorators is None:
                decorators = []
        if spec is None:
                spec = {}
        if initialization_info is None:
                initialization_info = {}
        scf_fact = ScfFactory(structure=structure, pseudos=pseudos, kppa=kppa, ecut=ecut, pawecutdg=pawecutdg,
                              nband=nband, accuracy=accuracy, spin_mode=spin_mode, smearing=smearing, charge=charge,
                              scf_algorithm=scf_algorithm, shift_mode=shift_mode, extra_abivars=extra_abivars,
                              decorators=decorators)

        hybrid_fact = HybridOneShotFromGsFactory(functional=hybrid_functional, ecutsigx=ecutsigx, gw_qprange=gw_qprange,
                                                 decorators=decorators, extra_abivars=extra_abivars)

        return cls(scf_fact, hybrid_fact, autoparal=autoparal, spec=spec, initialization_info=initialization_info)


class NscfFWWorkflow(AbstractFWWorkflow):
    def __init__(self, scf_input, nscf_input, autoparal=False, spec=None):

        if spec is None:
            spec = {}
        spec = dict(spec)
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)

        ion_task = ScfFWTask(scf_input, is_autoparal=autoparal)
        self.ion_fw = Firework(ion_task, spec=spec)

        ioncell_task = NscfFWTask(nscf_input, deps={ion_task.task_type: 'DEN'}, is_autoparal=autoparal)
        self.ioncell_fw = Firework(ioncell_task, spec=spec)

        self.wf = Workflow([self.ion_fw, self.ioncell_fw], {self.ion_fw: [self.ioncell_fw]})


class PhononFWWorkflow(AbstractFWWorkflow):
    workflow_class = 'PhononFWWorkflow'
    workflow_module = 'abipy.fworks.fw_workflows'

    def __init__(self, scf_inp, phonon_factory, autoparal=False, spec=None, initialization_info=None):
        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        start_task_index = 1

        rf = self.get_reduced_formula(scf_inp)

        scf_task = ScfFWTask(scf_inp, is_autoparal=autoparal)

        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)
            start_task_index = 'autoparal'

        spec['wf_task_index'] = 'scf_' + str(start_task_index)


        self.scf_fw = Firework(scf_task, spec=spec, name=rf+"_"+scf_task.task_type)

        ph_generation_task = GeneratePhononFlowFWTask(phonon_factory, previous_task_type=scf_task.task_type,
                                                      with_autoparal=autoparal)

        spec['wf_task_index'] = 'gen_ph'

        self.ph_generation_fw = Firework(ph_generation_task, spec=spec, name=rf+"_gen_ph")

        self.wf = Workflow([self.scf_fw, self.ph_generation_fw], {self.scf_fw: self.ph_generation_fw},
                           metadata={'workflow_class': self.workflow_class,
                                     'workflow_module': self.workflow_module})

    @classmethod
    def from_factory(cls, structure, pseudos, kppa=None, ecut=None, pawecutdg=None, nband=None, accuracy="normal",
                     spin_mode="polarized", smearing="fermi_dirac:0.1 eV", charge=0.0, scf_algorithm=None,
                     shift_mode="Symmetric", ph_ngqpt=None, with_ddk=True, with_dde=True, with_bec=False,
                     scf_tol=None, ph_tol=None, ddk_tol=None, dde_tol=None, extra_abivars=None, decorators=None,
                     autoparal=False, spec=None, initialization_info=None):

        if extra_abivars is None:
                extra_abivars = {}
        if decorators is None:
                decorators = []
        if spec is None:
                spec = {}
        if initialization_info is None:
                initialization_info = {}
        extra_abivars_scf = dict(extra_abivars)
        extra_abivars_scf['tolwfr'] = scf_tol if scf_tol else 1.e-22
        scf_fact = ScfForPhononsFactory(structure=structure, pseudos=pseudos, kppa=kppa, ecut=ecut, pawecutdg=pawecutdg,
                                        nband=nband, accuracy=accuracy, spin_mode=spin_mode, smearing=smearing,
                                        charge=charge, scf_algorithm=scf_algorithm, shift_mode=shift_mode,
                                        extra_abivars=extra_abivars_scf, decorators=decorators)

        phonon_fact = PhononsFromGsFactory(ph_ngqpt=ph_ngqpt, with_ddk=with_ddk, with_dde=with_dde, with_bec=with_bec,
                                           ph_tol=ph_tol, ddk_tol=ddk_tol, dde_tol=dde_tol, extra_abivars=extra_abivars,
                                           decorators=decorators)

        return cls(scf_fact, phonon_fact, autoparal=autoparal, spec=spec, initialization_info=initialization_info)


class PiezoElasticFWWorkflow(AbstractFWWorkflow):
    workflow_class = 'PiezoElasticFWWorkflow'
    workflow_module = 'abiflows.fireworks.workflows.abinit_workflows'

    def __init__(self, scf_inp, ddk_inp, rf_inp, autoparal=False, spec=None, initialization_info=None):
        if spec is None:
            spec = {}
        if initialization_info is None:
            initialization_info = {}
        rf = self.get_reduced_formula(scf_inp)

        scf_task = ScfFWTask(scf_inp, is_autoparal=autoparal)

        spec = dict(spec)
        spec['initialization_info'] = initialization_info
        if autoparal:
            spec = self.set_short_single_core_to_spec(spec)

        self.scf_fw = Firework(scf_task, spec=spec, name=rf+"_"+scf_task.task_type)

        ddk_task = DdkTask(ddk_inp, is_autoparal=autoparal, deps={scf_task.task_type: 'WFK'})

        ddk_fw_name = rf+ddk_task.task_type
        ddk_fw_name = ddk_fw_name[:8]
        self.ddk_fw = Firework(ddk_task, spec=spec, name=ddk_fw_name)

        rf_task = StrainPertTask(rf_inp, is_autoparal=autoparal, deps={scf_task.task_type: 'WFK', ddk_task.task_type: 'DDK'})

        rf_fw_name = rf+rf_task.task_type
        rf_fw_name = rf_fw_name[:8]
        self.rf_fw = Firework(rf_task, spec=spec, name=rf_fw_name)

        self.wf = Workflow(fireworks=[self.scf_fw, self.ddk_fw, self.rf_fw],
                           links_dict={self.scf_fw: self.ddk_fw, self.ddk_fw: self.rf_fw},
                           metadata={'workflow_class': self.workflow_class,
                                     'workflow_module': self.workflow_module})

        self.add_anaddb_task(scf_inp.structure)

    def add_anaddb_task(self, structure):
        spec = self.set_short_single_core_to_spec()
        anaddb_task = AnaDdbTask(AnaddbInput.piezo_elastic(structure))
        anaddb_fw = Firework([anaddb_task],
                             spec=spec,
                             name='anaddb')
        append_fw_to_wf(anaddb_fw, self.wf)

    def add_mrgddb_task(self, structure):
        spec = self.set_short_single_core_to_spec()
        spec['ddb_files_task_types'] = ['scf', 'strain_pert']
        mrgddb_task = MergeDdbTask()
        mrgddb_fw = Firework([mrgddb_task], spec=spec, name='mrgddb')
        append_fw_to_wf(mrgddb_fw, self.wf)

    @classmethod
    def get_elastic_tensor_and_history(cls, wf):
        assert wf.metadata['workflow_class'] == cls.workflow_class
        assert wf.metadata['workflow_module'] == cls.workflow_module

        final_fw_id = None
        for fw_id, fw in wf.id_fw.items():
            if fw.name == 'anaddb':
                final_fw_id = fw_id
        if final_fw_id is None:
            raise RuntimeError('Final anaddb task not found ...')
        myfw = wf.id_fw[final_fw_id]
        #TODO add a check on the state of the launches
        last_launch = (myfw.archived_launches + myfw.launches)[-1]
        #TODO add a cycle to find the instance of AbiFireTask?
        myfw.tasks[-1].set_workdir(workdir=last_launch.launch_dir)
        elastic_tensor = myfw.tasks[-1].get_elastic_tensor()
        history = loadfn(os.path.join(last_launch.launch_dir, 'history.json'))

        return {'elastic_properties': elastic_tensor.extended_dict(), 'history': history}

    @classmethod
    def get_all_elastic_tensors(cls, wf):
        assert wf.metadata['workflow_class'] == cls.workflow_class
        assert wf.metadata['workflow_module'] == cls.workflow_module

        final_fw_id = None
        for fw_id, fw in wf.id_fw.items():
            if fw.name == 'anaddb':
                final_fw_id = fw_id
        if final_fw_id is None:
            raise RuntimeError('Final anaddb task not found ...')
        myfw = wf.id_fw[final_fw_id]
        #TODO add a check on the state of the launches
        last_launch = (myfw.archived_launches + myfw.launches)[-1]
        #TODO add a cycle to find the instance of AbiFireTask?
        myfw.tasks[-1].set_workdir(workdir=last_launch.launch_dir)
        elastic_tensor = myfw.tasks[-1].get_elastic_tensor()
        history = loadfn(os.path.join(last_launch.launch_dir, 'history.json'))

        return {'elastic_properties': elastic_tensor.extended_dict(), 'history': history}

    @classmethod
    def from_factory(cls):
        raise NotImplemented('from factory method not yet implemented for piezoelasticworkflow')