"""Coordinator-only callable inventory; no method or generator is executed here."""
from functools import partial

from .biology import CapabilityRegistration, RuntimeSupportRegistry, load_biology_catalog


def biology_runtime_registry(catalog=None):
    """Register execution paths, not numerical competence or clinical qualification.

    A tool can audit a target whose estimator it cannot implement. Its declared
    method limits remain separately reportable and failures remain scoreable.
    Case-level scope belongs only to this coordinator inventory, never dispatch.
    """
    from .biology_datasets import biology_generator_registry
    from .biology_scoring import biology_scoring_registrations
    from .biology_tools_bulk import analyze_bulk_tool, bulk_tool_capabilities
    from .biology_tools_singlecell import analyze_singlecell_tool, singlecell_tool_capabilities
    catalog = catalog or load_biology_catalog()
    registrations = list(biology_generator_registry(catalog).registrations)
    registrations.extend(biology_scoring_registrations(catalog))
    bulk, cell = bulk_tool_capabilities(), singlecell_tool_capabilities()
    for template in catalog.templates:
        cellular = template.unit_hierarchy.cell_label is not None
        methods = cell if cellular else bulk
        for case in template.cases:
            scope = {item.target_id: item for item in case.target_scopes}
            for target in template.targets:
                for name in scope[target.target_id].tool_requirements or target.tool_requirements:
                    # Common QC/unit prerequisites use the same observed-input
                    # implementation for bulk and cellular measurements.
                    driver = analyze_singlecell_tool if cellular and name in methods else analyze_bulk_tool
                    if name not in methods and name not in bulk:
                        continue
                    registrations.append(CapabilityRegistration(kind="tool", capability_id=name,
                        template_id=template.template_id, case_ids=(case.case_id,),
                        target_ids=(target.target_id,), measurement=case.measurement,
                        handler=partial(driver, name)))
    return RuntimeSupportRegistry(tuple(registrations))


def biology_method_metadata():
    from .biology_tools_bulk import bulk_tool_capabilities
    from .biology_tools_singlecell import singlecell_tool_capabilities
    return [{"tool_name": name, "driver": driver, **metadata}
            for driver, methods in (("bulk", bulk_tool_capabilities()),
                                     ("singlecell", singlecell_tool_capabilities()))
            for name, metadata in methods.items()]
