"""Bulk-add remaining vars in level_type/system-gated datasets.

Walks constraints.json for each dataset, enumerates missing vars,
emits YAML rows using:

1. The catalog's existing (cds_variable -> nc_variable) map (from
   probes that already happened during the autopilot session).
2. A hand-curated extension table for cds_variables that haven't
   been probed yet but follow the ECMWF GRIB short-name convention.

For each gated dataset, per-row ``extras`` override the parent's
level_type / product_type / time_aggregation so the same dataset
key can host multiple level_type-scoped vars.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from earth2observe.ecmwf.constraints import fetch_constraints

CATALOG_PATH = Path("src/earth2observe/ecmwf/cds_data_catalog.yaml")

# Hand-curated extension to the existing catalog mapping. Values are
# (nc_variable, units, types). Pulled from ECMWF parameter database
# conventions; verified against ECMWF GRIB tables.
EXTRA_MAPPING: dict[str, tuple[str, str, str]] = {
    # CARRA-means analysis-based / forecast-based vars not yet in catalog
    "100m_wind_speed": ("si100", "m s**-1", "state"),
    "skin_reservoir_content": ("src", "kg m**-2", "state"),
    "snow_depth": ("sde", "m", "state"),
    "soil_temperature": ("sot", "K", "state"),
    "skin_temperature": ("skt", "K", "state"),
    "albedo": ("al", "%", "state"),
    "evaporation": ("eva", "kg m**-2", "flux"),
    "fog": ("fog", "%", "state"),
    "fraction_of_snow_cover": ("fscov", "Proportion", "state"),
    "graupel": ("grle", "kg kg**-1", "state"),
    "high_cloud_cover": ("hcc", "%", "state"),
    "land_sea_mask": ("lsm", "(0 - 1)", "state"),
    "low_cloud_cover": ("lcc", "%", "state"),
    "mean_sea_level_pressure": ("msl", "Pa", "state"),
    "medium_cloud_cover": ("mcc", "%", "state"),
    "orography": ("orog", "m", "state"),
    "precipitation_type": ("ptype", "(0 - 8)", "state"),
    "sea_ice_area_fraction": ("siconc", "(0 - 1)", "state"),
    "sea_ice_surface_temperature": ("ist", "K", "state"),
    "sea_ice_thickness": ("siti", "m", "state"),
    "sea_surface_temperature": ("sst", "K", "state"),
    "snow_albedo": ("asn", "%", "state"),
    "snow_density": ("rsn", "kg m**-3", "state"),
    "snow_depth_water_equivalent": ("sd", "kg m**-2", "state"),
    "snow_fall_water_equivalent": ("sf", "kg m**-2", "flux"),
    "snow_melt": ("snom", "kg m**-2", "flux"),
    "snow_on_ice_total_depth": ("soit", "m", "state"),
    "surface_latent_heat_flux": ("slhf", "J m**-2", "flux"),
    "surface_net_solar_radiation": ("ssr", "J m**-2", "flux"),
    "surface_net_thermal_radiation": ("str", "J m**-2", "flux"),
    "surface_pressure": ("sp", "Pa", "state"),
    "surface_roughness": ("sr", "m", "state"),
    "surface_runoff": ("sro", "kg m**-2", "flux"),
    "surface_sensible_heat_flux": ("sshf", "J m**-2", "flux"),
    "surface_solar_radiation_downwards": ("ssrd", "J m**-2", "flux"),
    "surface_thermal_radiation_downwards": ("strd", "J m**-2", "flux"),
    "thermal_surface_radiation_downwards": ("strd", "J m**-2", "flux"),
    "top_net_solar_radiation": ("tsr", "J m**-2", "flux"),
    "top_net_thermal_radiation": ("ttr", "J m**-2", "flux"),
    "total_cloud_cover": ("tcc", "%", "state"),
    "total_column_cloud_ice_water": ("tciw", "kg m**-2", "state"),
    "total_column_cloud_liquid_water": ("tclw", "kg m**-2", "state"),
    "total_column_graupel": ("tcgr", "kg m**-2", "state"),
    "total_column_integrated_water_vapour": ("tcwv", "kg m**-2", "state"),
    "total_precipitation": ("tp", "kg m**-2", "flux"),
    "visibility": ("vis", "m", "state"),
    "volumetric_soil_ice": ("vsi", "m**3 m**-3", "state"),
    "volumetric_soil_moisture": ("vsw", "m**3 m**-3", "state"),
    "10m_u_component_of_wind": ("u10", "m s**-1", "state"),
    "10m_v_component_of_wind": ("v10", "m s**-1", "state"),
    "10m_wind_direction": ("wdir10", "Degree true", "state"),
    "10m_wind_speed": ("si10", "m s**-1", "state"),
    "10m_wind_gust_since_previous_post_processing": (
        "fg10",
        "m s**-1",
        "state",
    ),
    "10m_eastward_wind_gust_since_previous_post_processing": (
        "efg10",
        "m s**-1",
        "flux",
    ),
    "10m_northward_wind_gust_since_previous_post_processing": (
        "nfg10",
        "m s**-1",
        "flux",
    ),
    "2m_relative_humidity": ("r2", "%", "state"),
    "2m_specific_humidity": ("sh2", "kg kg**-1", "state"),
    "2m_temperature": ("t2m", "K", "state"),
    "maximum_2m_temperature_since_previous_post_processing": (
        "mx2t",
        "K",
        "state",
    ),
    "minimum_2m_temperature_since_previous_post_processing": (
        "mn2t",
        "K",
        "state",
    ),
    "cloud_base": ("cdcb", "m", "state"),
    "cloud_cover": ("ccl", "%", "state"),
    "cloud_top": ("cdct", "m", "state"),
    "direct_solar_radiation": ("dirsrf", "J m**-2", "flux"),
    "geometric_vertical_velocity": ("wz", "m s**-1", "state"),
    "geopotential": ("z", "m**2 s**-2", "state"),
    "potential_vorticity": ("pv", "K m**2 kg**-1 s**-1", "state"),
    "pressure": ("pres", "Pa", "state"),
    "pseudo_adiabatic_potential_temperature": ("papt", "K", "state"),
    "relative_humidity": ("r", "%", "state"),
    "specific_cloud_ice_water_content": ("ciwc", "kg kg**-1", "state"),
    "specific_cloud_liquid_water_content": ("clwc", "kg kg**-1", "state"),
    "specific_cloud_rain_water_content": ("crwc", "kg kg**-1", "state"),
    "specific_cloud_snow_water_content": ("cswc", "kg kg**-1", "state"),
    "specific_humidity": ("q", "kg kg**-1", "state"),
    "specific_rain_water_content": ("crwc", "kg kg**-1", "state"),
    "specific_snow_water_content": ("cswc", "kg kg**-1", "state"),
    "temperature": ("t", "K", "state"),
    "turbulent_kinetic_energy": ("tke", "J kg**-1", "state"),
    "u_component_of_wind": ("u", "m s**-1", "state"),
    "v_component_of_wind": ("v", "m s**-1", "state"),
    "wind_direction": ("wdir", "Degree true", "state"),
    "wind_speed": ("si", "m s**-1", "state"),
    "lake_bottom_temperature": ("lblt", "K", "state"),
    "lake_depth": ("dl", "m", "state"),
    "lake_ice_depth": ("licd", "m", "state"),
    "lake_ice_temperature": ("lict", "K", "state"),
    "lake_mix_layer_depth": ("lmld", "m", "state"),
    "lake_mix_layer_temperature": ("lmlt", "K", "state"),
    "lake_shape_factor": ("lshf", "dimensionless", "state"),
    "lake_total_layer_temperature": ("ltlt", "K", "state"),
    "soil_heat_flux": ("sohf", "W m**-2", "flux"),
    "percolation": ("perc", "kg m**-2", "flux"),
    # Seasonal-specific aggregates
    "snowfall": ("mtsfr", "m of water equivalent s**-1", "flux"),
    "surface_solar_radiation": ("msnsrf", "W m**-2", "flux"),
    "sea_ice_cover": ("siconc", "(0 - 1)", "state"),
    "100m_u_component_of_wind": ("u100", "m s**-1", "state"),
    "100m_v_component_of_wind": ("v100", "m s**-1", "state"),
    "east_west_surface_stress_rate_of_accumulation": (
        "ewss",
        "N m**-2 s",
        "flux",
    ),
    "maximum_2m_temperature_in_the_last_24_hours": ("mx2t24", "K", "state"),
    "mean_sub_surface_runoff_rate": ("mssror", "kg m**-2 s**-1", "flux"),
    "mean_surface_runoff_rate": ("msror", "kg m**-2 s**-1", "flux"),
    "minimum_2m_temperature_in_the_last_24_hours": ("mn2t24", "K", "state"),
    "north_south_surface_stress_rate_of_accumulation": (
        "nsss",
        "N m**-2 s",
        "flux",
    ),
    "runoff": ("ro", "m", "flux"),
    "soil_temperature_level_1": ("stl1", "K", "state"),
    "solar_insolation_rate_of_accumulation": ("sira", "J m**-2", "flux"),
    "surface_thermal_radiation": ("str", "J m**-2", "flux"),
    "top_solar_radiation": ("tsr", "J m**-2", "flux"),
    "top_thermal_radiation": ("ttr", "J m**-2", "flux"),
    "total_column_water_vapour": ("tcwv", "kg m**-2", "state"),
    # Seasonal anomaly variants — postprocessed
    "10m_u_component_of_wind_anomaly": ("u10a", "m s**-1", "state"),
    "10m_v_component_of_wind_anomaly": ("v10a", "m s**-1", "state"),
    "10m_wind_speed_anomaly": ("si10a", "m s**-1", "state"),
    "2m_dewpoint_temperature_anomaly": ("d2a", "K", "state"),
    "2m_temperature_anomaly": ("t2a", "K", "state"),
    "100m_wind_speed_anomaly": ("si100a", "m s**-1", "state"),
    "100m_u_component_of_wind_anomaly": ("u100a", "m s**-1", "state"),
    "100m_v_component_of_wind_anomaly": ("v100a", "m s**-1", "state"),
    "evaporation_anomaly": ("ea", "kg m**-2 s**-1", "flux"),
    "geopotential_anomaly": ("za", "m**2 s**-2", "state"),
    "mean_sea_level_pressure_anomaly": ("msla", "Pa", "state"),
    "runoff_anomaly": ("roa", "m", "flux"),
    "snowfall_anomaly": ("sfa", "m of water equivalent s**-1", "flux"),
    "snow_depth_anomaly": ("sda", "m of water equivalent", "state"),
    "soil_temperature_level_1_anomaly": ("stl1a", "K", "state"),
    "surface_pressure_anomaly": ("spa", "Pa", "state"),
    "surface_solar_radiation_anomaly": ("ssra", "W m**-2", "flux"),
    "surface_thermal_radiation_anomaly": ("stra", "W m**-2", "flux"),
    "surface_solar_radiation_downwards_anomaly": ("ssrda", "W m**-2", "flux"),
    "surface_thermal_radiation_downwards_anomaly": ("strda", "W m**-2", "flux"),
    "surface_latent_heat_flux_anomaly": ("slhfa", "W m**-2", "flux"),
    "surface_sensible_heat_flux_anomaly": ("sshfa", "W m**-2", "flux"),
    "sea_surface_temperature_anomaly": ("ssta", "K", "state"),
    "sea_ice_cover_anomaly": ("sica", "(0 - 1)", "state"),
    "total_cloud_cover_anomaly": ("tcca", "(0 - 1)", "state"),
    "total_precipitation_anomaly": ("tpa", "m s**-1", "flux"),
    "top_solar_radiation_anomaly": ("tsra", "W m**-2", "flux"),
    "top_thermal_radiation_anomaly": ("ttra", "W m**-2", "flux"),
    "east_west_surface_stress_anomalous_rate_of_accumulation": (
        "ewssa",
        "N m**-2 s",
        "flux",
    ),
    "north_south_surface_stress_anomalous_rate_of_accumulation": (
        "nsssa",
        "N m**-2 s",
        "flux",
    ),
    "solar_insolation_anomalous_rate_of_accumulation": (
        "siraa",
        "J m**-2",
        "flux",
    ),
    "mean_sub_surface_runoff_rate_anomaly": (
        "mssrora",
        "kg m**-2 s**-1",
        "flux",
    ),
    "mean_surface_runoff_rate_anomaly": ("msrora", "kg m**-2 s**-1", "flux"),
    "10m_wind_gust_anomaly": ("10fga", "m s**-1", "state"),
    "maximum_2m_temperature_in_the_last_24_hours_anomaly": (
        "mx2t24a",
        "K",
        "state",
    ),
    "minimum_2m_temperature_in_the_last_24_hours_anomaly": (
        "mn2t24a",
        "K",
        "state",
    ),
    # Ocean
    "depth_average_potential_temperature_of_upper_300m": ("thetaot300", "K", "state"),
    "depth_average_potential_temperature_of_upper_500m": ("thetaot500", "K", "state"),
    "depth_average_salinity_of_upper_300m": ("sot300", "1e-3", "state"),
    "depth_average_salinity_of_upper_500m": ("sot500", "1e-3", "state"),
    "mixed_layer_depth_0_5_reference": ("mlotst", "m", "state"),
    "mixed_layer_depth": ("mlotst", "m", "state"),
    "sea_water_potential_temperature_at_sea_floor": ("bottomT", "K", "state"),
    "sea_surface_height_above_geoid": ("zos", "m", "state"),
    "sea_water_potential_temperature": ("thetao", "K", "state"),
    "sea_water_salinity": ("so", "1e-3", "state"),
    "depth_of_the_20_isotherm": ("d20", "m", "state"),
    # More postprocessed anomalies
    "100_metre_u_wind_component_anomaly": ("u100a", "m s**-1", "state"),
    "100_metre_v_wind_component_anomaly": ("v100a", "m s**-1", "state"),
    "100_metre_wind_speed_anomaly": ("si100a", "m s**-1", "state"),
    "evaporation_anomalous_rate_of_accumulation": ("ea", "kg m**-2 s**-1", "flux"),
    "runoff_anomalous_rate_of_accumulation": ("roa", "kg m**-2 s**-1", "flux"),
    "snow_density_anomaly": ("rsna", "kg m**-3", "state"),
    "snowfall_anomalous_rate_of_accumulation": ("sfa", "kg m**-2 s**-1", "flux"),
    "soil_temperature_anomaly_level_1": ("stl1a", "K", "state"),
    "surface_latent_heat_flux_anomalous_rate_of_accumulation": ("slhfa", "W m**-2", "flux"),
    "surface_sensible_heat_flux_anomalous_rate_of_accumulation": ("sshfa", "W m**-2", "flux"),
    "surface_solar_radiation_anomalous_rate_of_accumulation": ("ssra", "W m**-2", "flux"),
    "surface_solar_radiation_downwards_anomalous_rate_of_accumulation": ("ssrda", "W m**-2", "flux"),
    "surface_thermal_radiation_anomalous_rate_of_accumulation": ("stra", "W m**-2", "flux"),
    "surface_thermal_radiation_downwards_anomalous_rate_of_accumulation": ("strda", "W m**-2", "flux"),
    "top_solar_radiation_anomalous_rate_of_accumulation": ("tsra", "W m**-2", "flux"),
    "top_thermal_radiation_anomalous_rate_of_accumulation": ("ttra", "W m**-2", "flux"),
    "total_column_ice_water_anomaly": ("tciwa", "kg m**-2", "state"),
    "total_column_liquid_water_anomaly": ("tclwa", "kg m**-2", "state"),
    "total_column_water_vapour_anomaly": ("tcwva", "kg m**-2", "state"),
    "total_precipitation_anomalous_rate_of_accumulation": ("tpa", "m s**-1", "flux"),
    # Postprocessed pressure-levels anomalies
    "specific_humidity_anomaly": ("qa", "kg kg**-1", "state"),
    "temperature_anomaly": ("ta", "K", "state"),
    "u_component_of_wind_anomaly": ("ua", "m s**-1", "state"),
    "v_component_of_wind_anomaly": ("va", "m s**-1", "state"),
    # Ocean
    "depth_of_14_c_isotherm": ("d14", "m", "state"),
    "depth_of_17_c_isotherm": ("d17", "m", "state"),
    "depth_of_20_c_isotherm": ("d20", "m", "state"),
    "depth_of_26_c_isotherm": ("d26", "m", "state"),
    "depth_of_28_c_isotherm": ("d28", "m", "state"),
    "mixed_layer_depth_0_01": ("mlotst001", "m", "state"),
    "mixed_layer_depth_0_03": ("mlotst003", "m", "state"),
    "sea_surface_salinity": ("sos", "1e-3", "state"),
}


def slug(name: str) -> str:
    """Convert cds_variable to a YAML key (kebab-case)."""
    return name.lower().replace("_", "-").replace(",", "").replace(" ", "-")


def collect_existing(catalog: dict[str, Any], ds_name: str) -> set[str]:
    block = catalog["datasets"].get(ds_name, {})
    return {
        v["cds_variable"]
        for v in block.get("variables", {}).values()
        if isinstance(v, dict) and "cds_variable" in v
    }


def lookup(cv: str, catalog_known: dict[str, tuple[str, str, str]]) -> tuple[str, str, str] | None:
    if cv in catalog_known:
        return catalog_known[cv]
    if cv in EXTRA_MAPPING:
        return EXTRA_MAPPING[cv]
    return None


def build_known(catalog: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    known: dict[str, tuple[str, str, str]] = {}
    for ds_body in catalog["datasets"].values():
        for vbody in ds_body.get("variables", {}).values():
            if not isinstance(vbody, dict):
                continue
            cv = vbody.get("cds_variable")
            nv = vbody.get("nc_variable")
            if cv and nv:
                known[cv] = (nv, vbody.get("units", ""), vbody.get("types", "state"))
    return known


def _emit_var_block(
    suffix: str,
    cv: str,
    nv: str,
    un: str,
    ty: str,
    extras: dict[str, Any] | None = None,
) -> str:
    key = f"{slug(cv)}{suffix}"
    lines = [
        f'      "{key}":',
        f"        cds_variable: {cv}",
        f"        nc_variable: {nv}",
        f"        types: {ty}",
        f'        units: "{un}"',
    ]
    if extras:
        lines.append("        extras:")
        for k, v in extras.items():
            if isinstance(v, list):
                lines.append(f"          {k}: {v}")
            else:
                lines.append(f'          {k}: "{v}"' if isinstance(v, str) else f"          {k}: {v}")
    return "\n".join(lines) + "\n"


def report_dataset(name: str, missing: Iterable[str], known: dict[str, tuple[str, str, str]]) -> tuple[list[str], list[str]]:
    """Return (resolvable_blocks, unresolved_vars)."""
    blocks: list[str] = []
    unresolved: list[str] = []
    for cv in sorted(missing):
        m = lookup(cv, known)
        if m is None:
            unresolved.append(cv)
        else:
            blocks.append((cv, m))  # type: ignore
    return blocks, unresolved


def main() -> int:
    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    known = build_known(catalog)
    print(f"Catalog known mappings: {len(known)}")
    print(f"EXTRA_MAPPING entries: {len(EXTRA_MAPPING)}")
    print()

    targets = [
        "reanalysis-carra-means",
        "reanalysis-pan-carra",
        "reanalysis-pan-carra-means",
        "seasonal-monthly-pressure-levels",
        "seasonal-original-single-levels",
        "seasonal-original-pressure-levels",
        "seasonal-postprocessed-single-levels",
        "seasonal-postprocessed-pressure-levels",
        "seasonal-monthly-ocean",
    ]

    for ds in targets:
        try:
            constraints = fetch_constraints(ds)
        except Exception as e:
            print(f"=== {ds}: FETCH FAILED — {e} ===")
            continue
        all_form_vars: set[str] = set()
        for entry in constraints:
            all_form_vars.update(entry.get("variable", []))
        in_catalog = collect_existing(catalog, ds)
        missing = sorted(all_form_vars - in_catalog)
        resolvable: list[tuple[str, tuple[str, str, str]]] = []
        unresolved: list[str] = []
        for cv in missing:
            m = lookup(cv, known)
            if m is None:
                unresolved.append(cv)
            else:
                resolvable.append((cv, m))
        print(
            f"=== {ds}: form={len(all_form_vars)}, in_cat={len(in_catalog)}, "
            f"missing={len(missing)}, resolvable={len(resolvable)}, unresolved={len(unresolved)} ==="
        )
        if unresolved:
            for u in unresolved[:5]:
                print(f"    UNRESOLVED: {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
