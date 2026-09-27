"""Display roles for a live capture, separate from certified search domains."""


def display_snapshot(project, layers, derived_features=()):
    source = project.source_geometry or project.geometry
    if source is None:
        raise ValueError("Карта захвата AutoCAD недоступна")
    features = []
    decisions = {'/'.join((*item.source.instance_chain, item.source.handle)): item.interpretation
                 for item in project.source_file.object_decisions} if project.source_file else {}
    originals = [feature for feature in source.feature_collection.get("features", [])
                 if not feature.get("properties", {}).get("source_native_face_id")]
    for feature in (*originals, *derived_features):
        props = dict(feature.get("properties", {}))
        if props.get("kind") in {"allowed", "forbidden", "planting_area"}:
            continue
        layer = layers.get(props.get("source_layer"))
        if layer:
            props["kind"] = layer.mapped_kind or "source_context"
        route = '/'.join((*props.get('source_instance_chain', []), props.get('source_handle', '')))
        if route in decisions:
            props['source_object_interpretation'] = decisions[route]
            if decisions[route] == 'reference':
                props['kind'] = 'source_context'
        features.append({**feature, "properties": props})
    return source.model_copy(
        update={
            "feature_collection": {"type": "FeatureCollection", "features": features},
            "calculation_scope": "available_data",
            "allowed_area_m2": None,
            "site_area_m2": None,
            "planning_area_m2": None,
        }
    )
