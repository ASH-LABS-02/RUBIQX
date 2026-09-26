# ARGUS Blender Simulations

> **⚠️ SIMULATED, not real flight footage or real detection output.** These scenes visualise
> the ARGUS mission concept. They do not simulate flight physics, sensor noise or model inference.

| File | What it shows |
|---|---|
| `argus_disaster_sim_v002.blend` | Disaster zone: collapsed structures, fire and smoke, drone sweep |
| `argus_rescue_cinematic_v001.blend` | Survivor detection sequence: drone finds a person, detection overlay, alert to rescue team |
| `argus_computed_slam_v004.blend` | Mapping visualisation: how the drone builds a map of the area as it flies |

## Details

- Made in Blender 5.2, rendered with Eevee.
- Textures are packed into each file (File → External Data → Pack Resources), so they open without missing images.
- Backup files (`*.blend1`, `*.blend2`) are excluded by `.gitignore`.
- Asset credits: *add any downloaded models or textures here, with their licence.*

## Real results

For what ARGUS has actually demonstrated, see the ORB-SLAM3 prototype in [`../../SLAM/`](../../SLAM/)
and the trained models in [`../../ml-models/`](../../ml-models/).
