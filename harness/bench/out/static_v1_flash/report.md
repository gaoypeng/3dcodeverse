# bench report — static_objects_v1

runs: 24  ·  generator: api-agent:gemini:gemini-3.7-flash  ·  judge: gemini:gemini-3.1-pro-preview  ·  rounds ≤ 3

### overall

| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| all | 24 | 19 | 0.643 | 0.718 | 0.750 | +0.075 | 58% | 1.10 | 15.6 | 12 |

### by tier

| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| easy | 8 | 7 | 0.751 | 0.861 | 0.850 | +0.110 | 100% | 0.56 | 9.3 | 1 |
| medium | 8 | 5 | 0.545 | 0.643 | 0.600 | +0.098 | 20% | 1.18 | 16.1 | 7 |
| hard | 8 | 7 | 0.606 | 0.630 | 0.600 | +0.024 | 43% | 1.56 | 21.4 | 4 |

### by category

| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| appliances | 3 | 2 | 0.741 | 0.841 | 0.841 | +0.101 | 100% | 0.42 | 10.3 | 1 |
| buildings | 3 | 3 | 0.667 | 0.726 | 0.746 | +0.060 | 67% | 1.14 | 12.9 | 1 |
| furniture | 3 | 2 | 0.725 | 0.750 | 0.750 | +0.025 | 100% | 0.51 | 9.3 | 1 |
| musical_instruments | 3 | 1 | 0.600 | 0.600 | 0.600 | +0.000 | 0% | 0.63 | 9.1 | 2 |
| plants | 3 | 2 | 0.633 | 0.633 | 0.633 | +0.000 | 50% | 0.87 | 12.5 | 2 |
| tools | 3 | 3 | 0.662 | 0.685 | 0.576 | +0.023 | 33% | 1.83 | 21.1 | 2 |
| toys | 3 | 3 | 0.735 | 0.839 | 0.867 | +0.104 | 67% | 1.20 | 14.7 | 1 |
| vehicles | 3 | 3 | 0.411 | 0.616 | 0.526 | +0.204 | 33% | 2.19 | 35.0 | 2 |

### per prompt

| id | tier | category | baseline | final | passed | rounds | $ | min | status |
|---|---|---|---|---|---|---|---|---|---|
| appl_easy_kettle | easy | appliances | 0.600 | 0.801 | yes | 2 | 0.94 | 22.6 | passed |
| bld_easy_shed | easy | buildings | 0.833 | 0.833 | yes | 1 | 0.41 | 6.2 | passed |
| furn_easy_stool | easy | furniture | 0.750 | 0.750 | yes | 1 | 0.30 | 4.5 | passed |
| mus_easy_drum | easy | musical_instruments | - | - | - | 0 | 0.00 | 1.1 | error ⚠ |
| plant_easy_cactus | easy | plants | 0.883 | 0.883 | yes | 1 | 0.44 | 10.0 | passed |
| tool_easy_hammer | easy | tools | 0.944 | 0.944 | yes | 1 | 0.40 | 5.6 | passed |
| toy_easy_blocks | easy | toys | 0.964 | 0.964 | yes | 1 | 0.29 | 4.1 | passed |
| veh_easy_toy_car | easy | vehicles | 0.282 | 0.850 | yes | 3 | 1.68 | 20.8 | passed |
| appl_med_toaster | medium | appliances | 0.881 | 0.881 | yes | 1 | 0.32 | 5.5 | passed |
| bld_med_lighthouse | medium | buildings | 0.467 | 0.600 | no | 2 | 2.28 | 21.9 | budget ⚠ |
| furn_med_dining_chair | medium | furniture | - | - | - | 0 | 0.00 | 0.9 | error ⚠ |
| mus_med_acoustic_guitar | medium | musical_instruments | - | - | - | 0 | 0.00 | 1.5 | error ⚠ |
| plant_med_palm | medium | plants | - | - | - | 0 | 0.00 | 1.4 | error ⚠ |
| tool_med_hand_drill | medium | tools | 0.576 | 0.576 | no | 1 | 2.06 | 22.3 | budget ⚠ |
| toy_med_rocking_horse | medium | toys | 0.374 | 0.685 | no | 2 | 2.62 | 28.8 | budget ⚠ |
| veh_med_pickup | medium | vehicles | 0.426 | 0.471 | no | 2 | 2.14 | 46.5 | budget ⚠ |
| appl_hard_espresso | hard | appliances | - | - | - | 0 | 0.00 | 2.7 | error ⚠ |
| bld_hard_windmill | hard | buildings | 0.700 | 0.746 | yes | 2 | 0.73 | 10.7 | passed |
| furn_hard_rolltop_desk | hard | furniture | 0.700 | 0.750 | yes | 3 | 1.22 | 22.5 | passed |
| mus_hard_drum_kit | hard | musical_instruments | 0.600 | 0.600 | no | 3 | 1.89 | 24.7 | plateau |
| plant_hard_bonsai | hard | plants | 0.384 | 0.384 | no | 1 | 2.18 | 26.1 | budget ⚠ |
| tool_hard_bench_vise | hard | tools | 0.466 | 0.536 | no | 2 | 3.02 | 35.4 | budget ⚠ |
| toy_hard_train | hard | toys | 0.867 | 0.867 | yes | 1 | 0.70 | 11.0 | passed |
| veh_hard_tractor | hard | vehicles | 0.526 | 0.526 | no | 2 | 2.75 | 37.8 | budget ⚠ |
