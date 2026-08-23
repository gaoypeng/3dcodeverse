# bench report — articulated_v1

runs: 12  ·  generator: api-agent:gemini:gemini-3.7-flash  ·  judge: gemini:gemini-3.1-pro-preview  ·  rounds ≤ 3

### overall

| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| all | 12 | 9 | 0.618 | 0.800 | 0.942 | +0.182 | 67% | 0.94 | 19.1 | 6 |

### by tier

| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| easy | 4 | 4 | 0.788 | 0.875 | 0.955 | +0.086 | 75% | 0.97 | 19.9 | 1 |
| medium | 4 | 3 | 0.635 | 0.719 | 0.750 | +0.083 | 67% | 1.03 | 16.6 | 2 |
| hard | 4 | 2 | 0.252 | 0.771 | 0.771 | +0.519 | 50% | 0.83 | 20.7 | 3 |

### by category

| group | n | scored | baseline | final | median | Δ | pass | $/run | min/run | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| architecture | 1 | 1 | 0.066 | 0.600 | 0.600 | +0.534 | 0% | 2.32 | 54.9 | 1 |
| electronics | 1 | 1 | 0.600 | 0.946 | 0.946 | +0.346 | 100% | 1.12 | 35.1 | 0 |
| furniture | 4 | 3 | 0.790 | 0.790 | 0.964 | +0.000 | 67% | 0.67 | 11.2 | 2 |
| garden | 1 | 1 | 0.988 | 0.988 | 0.988 | +0.000 | 100% | 0.72 | 7.8 | 0 |
| lighting | 1 | 1 | 0.500 | 0.750 | 0.750 | +0.250 | 100% | 1.39 | 23.1 | 0 |
| props | 1 | 1 | 0.438 | 0.942 | 0.942 | +0.504 | 100% | 0.98 | 25.6 | 0 |
| tools | 2 | 1 | 0.600 | 0.600 | 0.600 | +0.000 | 0% | 1.04 | 18.1 | 2 |
| vehicles | 1 | 0 | - | - | - | - | - | 0.00 | 1.1 | 1 |

### per prompt

| id | tier | category | baseline | final | passed | rounds | $ | min | status |
|---|---|---|---|---|---|---|---|---|---|
| art_easy_cabinet_door | easy | furniture | 0.964 | 0.964 | yes | 1 | 0.30 | 4.1 | passed |
| art_easy_drawer_unit | easy | furniture | 0.988 | 0.988 | yes | 1 | 0.37 | 5.3 | passed |
| art_easy_laptop | easy | electronics | 0.600 | 0.946 | yes | 3 | 1.12 | 35.1 | passed |
| art_easy_scissors | easy | tools | 0.600 | 0.600 | no | 2 | 2.07 | 35.0 | budget ⚠ |
| art_med_desk_lamp | medium | lighting | 0.500 | 0.750 | yes | 2 | 1.39 | 23.1 | passed |
| art_med_folding_chair | medium | furniture | 0.418 | 0.418 | no | 1 | 2.00 | 34.5 | budget ⚠ |
| art_med_toolbox | medium | tools | - | - | - | 0 | 0.00 | 1.1 | error ⚠ |
| art_med_wheelbarrow | medium | garden | 0.988 | 0.988 | yes | 1 | 0.72 | 7.8 | passed |
| art_hard_bicycle_wheel_stand | hard | vehicles | - | - | - | 0 | 0.00 | 1.1 | error ⚠ |
| art_hard_door_handle | hard | architecture | 0.066 | 0.600 | no | 3 | 2.32 | 54.9 | budget ⚠ |
| art_hard_swivel_chair | hard | furniture | - | - | - | 0 | 0.00 | 1.0 | error ⚠ |
| art_hard_treasure_chest | hard | props | 0.438 | 0.942 | yes | 2 | 0.98 | 25.6 | passed |
