"""Shared builder for the castle-placement pair (forward_castle / safe_castle). Same trigger -- a
player who built a Castle in Castle Age as their PRIMARY building (before any additional TC of the
Castle Age) -- split by a distance test: forward = the castle is closer to the nearest opponent's
home TC than to the player's own home TC; safe = otherwise. Needs extract v3 building positions."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req


def make_castle(*, key, title, want_forward, trigger_spec):
    def trigger(game, pnum):
        cp = gd.castle_placement(game, pnum)
        return cp is not None and cp[0] is want_forward

    def factors(game, pnum):
        p = gd.player(game, pnum) or {}
        cp = gd.castle_placement(game, pnum)
        castle = gd.primary_castle(game, pnum)
        _, dist_own, dist_enemy = cp if cp else (None, None, None)
        return {
            "castle_placed_s": float(castle["t_s"]) if castle else None,
            "castle_s": float(p["castle_s"]) if p.get("castle_s") is not None else None,
            "dist_to_own_tc": round(dist_own, 1) if dist_own is not None else None,
            "dist_to_enemy_tc": round(dist_enemy, 1) if dist_enemy is not None else None,
            "enemy_over_own_ratio": round(dist_enemy / dist_own, 2) if (dist_own and dist_enemy is not None) else None,
            "reached_imperial": 1.0 if p.get("imperial_s") is not None else 0.0,
        }

    factor_specs = [
        dict(metric="castle_placed_s", label="Castle placed", kind="seconds"),
        dict(metric="castle_s", label="Castle click", kind="seconds"),
        dict(metric="dist_to_own_tc", label="Distance to own TC", kind="count"),
        dict(metric="dist_to_enemy_tc", label="Distance to nearest enemy TC", kind="count"),
        dict(metric="enemy_over_own_ratio", label="Enemy/own distance ratio", kind="count"),
        dict(metric="reached_imperial", label="Reached Imperial", kind="percent"),
    ]

    return Classification(
        key=key, title=title, version=1, trigger_spec=trigger_spec,
        requirements=[
            req("castle_position", source="extract.players.castle_builds (x,y,t_s)", status="available",
                note="first Castle built in Castle Age before any castle-age TC; added in extract v3"),
            req("home_tc_position", source="extract.players.tc_builds + start_tc_xy", status="available",
                note="last TC placed before Castle, else the pre-placed starting TC; extract v3"),
            req("opponent_home_tc", source="extract.players[other team].home TC position", status="available",
                note="cross-player: nearest enemy home TC; extract v3"),
            req("castle_click_s", source="extract.players.castle_s", status="available"),
            req("winner", source="extract.players.winner", status="available",
                note="outcome dimension; consumed by the runner (shape.result_row), not trigger/factors"),
        ],
        trigger=trigger, factors=factors, factor_specs=factor_specs,
    )
