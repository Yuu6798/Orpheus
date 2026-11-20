"""mv audio（曲の音から作る記録。実験的）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError


def _audio_parser(commands):
    """mv audio（実験的）: 曲の音から作る記録（拍の格子・打点・音の終わり・低音が抜ける所）を、音源か analysis.json の隣の別ファイルに
    書く。打点からは fx-shot の光の強さのキーも作る（keys）。"""
    audio = commands.add_parser("audio", help="experimental: records made from the song audio (beat grid, hits), written beside "
                                              "the audio or analysis.json (estimated values), and light keys from the hits")
    sub = audio.add_subparsers(dest="audio_command", required=True)
    grid = sub.add_parser("grid", help="fit a constant-tempo beat grid to the onsets: beats, downbeats and drift (estimated)")
    grid.add_argument("source", type=Path, help="audio file, or a project folder with analysis.json (then analysis.grid.json is "
                                                "written beside it)")
    grid.add_argument("--bpm-min", type=float, help="lowest bpm to try (searched in 0.02 steps)")
    grid.add_argument("--bpm-max", type=float, help="bpm to stop before")
    grid.add_argument("--bpm", type=float, help="use this grid instead of searching (with --phase): records beats, downbeats and drift")
    grid.add_argument("--phase", type=float, help="time of beat 0 in seconds (with --bpm)")
    grid.add_argument("--downbeat", choices=["onset", "chroma"], default="onset",
                      help="how to pick the downbeat: onset strength per beat phase (default) or chord change on the 8th grid")
    grid.add_argument("--beats-per-bar", type=int, default=4, help="beats per bar for the downbeat phase (default 4)")
    grid.add_argument("--out", type=Path, help="record to write (default: <audio stem>.grid.json beside the audio, or "
                                               "<project>/analysis.grid.json)")
    grid.add_argument("--fair-refine", action="store_true",
                      help="re-score the coarse best with the refine window before refining (default: the production way)")
    grid.add_argument("--force", action="store_true", help="overwrite a record whose contents differ")
    hits = sub.add_parser("hits", help="strong hits (sparse) and band-difference onsets (dense, with strength), estimated")
    hits.add_argument("source", type=Path, help="audio file, or a project folder with analysis.json (then analysis.hits.json is "
                                                "written beside it)")
    hits.add_argument("--grid", type=Path, help="mv audio grid record of the same audio: adds bar and beat positions")
    hits.add_argument("--strong-shift-ms", type=float, default=-16.7,
                      help="added to the strong hit times (default -16.7: the measured detection delay)")
    hits.add_argument("--onset-shift-ms", type=float, default=18.7,
                      help="added to the onset times (default 18.7: the measured detection lead)")
    hits.add_argument("--out", type=Path, help="record to write (default: <audio stem>.hits.json beside the audio, or "
                                               "<project>/analysis.hits.json)")
    hits.add_argument("--force", action="store_true", help="overwrite a record whose contents differ")
    keys = sub.add_parser("keys", help="light intensity keys from the hits: writes a copy of an fx-shot spec with one light's "
                                       "intensity keyed to the hits")
    keys.add_argument("hits", type=Path, help="mv audio hits record")
    keys.add_argument("--spec", type=Path, required=True, help="fx-shot spec whose light gets the keys (not changed)")
    keys.add_argument("--light", required=True, help="id of the light in the spec")
    keys.add_argument("--start", type=float, help="song time of the shot's 0 s (default: spec audio.offset_s, when the spec "
                                                  "audio is the same file as the hits record)")
    keys.add_argument("--from", dest="source", choices=["strong", "onsets"], default="strong", help="which hit list (default strong)")
    keys.add_argument("--min-strength", type=float, help="skip hits weaker than this (not in the production rule)")
    keys.add_argument("--section", type=float, nargs=2, action="append", metavar=("FROM", "TO"),
                      help="use only hits in [FROM, TO) song seconds (repeatable; default: the whole song)")
    keys.add_argument("--min-gap-s", type=float, default=1.6, help="skip a hit within this many seconds of the last used one (default 1.6)")
    keys.add_argument("--base", type=float, default=0.0, help="intensity between flashes (default 0)")
    keys.add_argument("--gain", type=float, default=1.0, help="intensity per unit of flash (the flash peaks near 1, capped at 1.2)")
    keys.add_argument("--by-strength", action="store_true", help="scale each flash by the hit strength (not in the production rule)")
    keys.add_argument("--out", type=Path, help="spec to write, in the same folder as --spec (default: <spec stem>_keys.json)")
    keys.add_argument("--force", action="store_true", help="overwrite a spec whose contents differ")
    end = sub.add_parser("end", help="when the sound ends: the last 0.1 s window whose loudness is within --floor-db of the "
                                     "loudest window (measured), and the digital silence after it")
    end.add_argument("source", type=Path, help="audio file, or a project folder with analysis.json (then analysis.end.json is "
                                               "written beside it)")
    end.add_argument("--window", type=float, default=0.1, help="window length in seconds (default 0.1)")
    end.add_argument("--floor-db", type=float, default=-60.0, help="a window sounds when within this many dB of the loudest (default -60)")
    end.add_argument("--out", type=Path, help="record to write (default: <audio stem>.end.json beside the audio)")
    end.add_argument("--force", action="store_true", help="overwrite a record whose contents differ")
    lows = sub.add_parser("lows", help="where the bass drops out: beats (with --grid) or windows whose low band is --drop-db below "
                                       "the median while the sound goes on (measured)")
    lows.add_argument("source", type=Path, help="audio file, or a project folder with analysis.json (then analysis.lows.json is "
                                                "written beside it)")
    lows.add_argument("--grid", type=Path, help="mv audio grid record of the same audio: one window per beat")
    lows.add_argument("--window", type=float, default=0.5, help="window length in seconds without --grid (default 0.5)")
    lows.add_argument("--band", type=float, nargs=2, default=[20.0, 150.0], metavar=("LOW_HZ", "HIGH_HZ"),
                      help="low band in Hz (default 20 150)")
    lows.add_argument("--drop-db", type=float, default=10.0,
                      help="a window drops out when its low band is this many dB below the median (default 10)")
    lows.add_argument("--floor-db", type=float, default=-30.0,
                      help="a window sounds when its total level is within this many dB of the loudest (default -30)")
    lows.add_argument("--out", type=Path, help="record to write (default: <audio stem>.lows.json beside the audio)")
    lows.add_argument("--force", action="store_true", help="overwrite a record whose contents differ")
    features = sub.add_parser("features", help="per-frame 3-band FFT envelopes (bass/mid/high), RMS, onset, and optional beat pulse (measured)")
    features.add_argument("source", type=Path, help="audio file, or a project folder with analysis.json (then analysis.features.json is "
                                                    "written beside it)")
    features.add_argument("--fps", type=float, default=30.0, help="frames per second to sample (default 30)")
    features.add_argument("--grid", type=Path, help="mv audio grid record of the same audio: adds beat_num and beat_pulse")
    features.add_argument("--bass-hz", type=float, nargs=2, default=[25.0, 140.0], metavar=("LOW_HZ", "HIGH_HZ"),
                          help="bass band in Hz (default 25 140)")
    features.add_argument("--mid-hz", type=float, nargs=2, default=[250.0, 2600.0], metavar=("LOW_HZ", "HIGH_HZ"),
                          help="mid band in Hz (default 250 2600)")
    features.add_argument("--high-hz", type=float, nargs=2, default=[4000.0, 10500.0], metavar=("LOW_HZ", "HIGH_HZ"),
                          help="high band in Hz (default 4000 10500)")
    features.add_argument("--smooth", type=float, default=0.35, help="decay smoothing factor in (0, 1] (default 0.35)")
    features.add_argument("--out", type=Path, help="record to write (default: <audio stem>.features.json beside the audio)")
    features.add_argument("--force", action="store_true", help="overwrite a record whose contents differ")


def audio_command(args):
    if args.audio_command == "grid":
        from ..audio import grid
        return grid.run(args.source, args.bpm_min, args.bpm_max, args.out, args.beats_per_bar, args.fair_refine, args.force,
                        args.bpm, args.phase, args.downbeat)
    if args.audio_command == "hits":
        from ..audio import hits
        return hits.run(args.source, args.grid, args.out, args.force, args.strong_shift_ms, args.onset_shift_ms)
    if args.audio_command == "keys":
        from ..audio import keys
        return keys.run(args.hits, args.spec, args.light, args.out, args.start, args.source, args.min_strength, args.section,
                        args.min_gap_s, args.base, args.gain, args.by_strength, args.force)
    if args.audio_command == "end":
        from ..audio import end
        return end.run(args.source, args.out, args.window, args.floor_db, args.force)
    if args.audio_command == "lows":
        from ..audio import lows
        return lows.run(args.source, args.grid, args.out, args.window, tuple(args.band), args.drop_db, args.floor_db, args.force)
    if args.audio_command == "features":
        from ..audio import features
        return features.run(args.source, args.fps, args.grid, args.out, tuple(args.bass_hz), tuple(args.mid_hz),
                            tuple(args.high_hz), args.smooth, args.force)
    raise PipelineError(f"Unsupported audio command: {args.audio_command}")
