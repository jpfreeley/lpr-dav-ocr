#!/usr/bin/env bash
# Pause, resume or stop every background lpr job without losing progress.
# usage: scripts/pause.sh pause|resume|stop|status
# pause/resume use SIGSTOP/SIGCONT (memory stays held). stop frees the memory; finished clips are
# already saved and `lpr process` resumes from the last finished clip.
# The [x] in the patterns keeps pgrep from matching this script's own command line.
PAT='lpr_dav_ocr\.[p]ipeline|lpr_dav_ocr\.second_[p]ass|lpr_dav_ocr\.[w]atch|bin/lpr [p]rocess|bin/lpr second-[p]ass|bin/lpr [w]atch|run-day[s]\.sh|follow-second-pas[s]\.sh|multiprocessing\.[s]pawn'
kids() { for p in $(pgrep -P "$1"); do echo "$p"; kids "$p"; done; }
all() { for r in $(pgrep -f "$PAT"); do echo "$r"; kids "$r"; done | sort -u; }
case "${1:-status}" in
  pause)  n=0; for p in $(all); do kill -STOP "$p" 2>/dev/null && n=$((n+1)); done; echo "paused $n processes" ;;
  resume) n=0; for p in $(all); do kill -CONT "$p" 2>/dev/null && n=$((n+1)); done; echo "resumed $n processes" ;;
  stop)   n=0; for p in $(all); do kill -9 "$p" 2>/dev/null && n=$((n+1)); done; echo "stopped $n processes" ;;
  *) for p in $(all); do ps -o pid=,state=,rss= -p "$p"; done | awk '{s[$2]++; m+=$3} END {for (k in s) printf "state %s: %d processes\n", k, s[k]; printf "memory held: %.1f GB\n", m/1048576}' ;;
esac
