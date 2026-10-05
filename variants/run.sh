#!/bin/zsh
# Sweep over --label-sigma, --curve-sigma, --smoothing (grid). Basis: --colors 10 --regions 800 --work-size 2000 --line-width 0.3
# Each job peaks ~1.7 GB RSS. On 16 GB keep <=4 concurrent. ponytail: xargs -P gates it, not a hand-rolled jobs counter.
BASE=(--min-area 50 --colors 12 --work-size 2000 --line-width 0.3 --edge-backend classical --no-report -q)
LABEL_SIGMA=(1 2 3)
CURVE_SIGMA=(10 15 20)
SMOOTHING=(0.5)
JOBS=${JOBS:-4}   # concurrent jobs; override: JOBS=4 ./run.sh

cd ${0:A:h}/..    # repo root, so relative paths resolve regardless of CWD

for ls in $LABEL_SIGMA; do for cs in $CURVE_SIGMA; do for sm in $SMOOTHING; do
  print -r -- "$ls $cs $sm"
done; done; done | xargs -P $JOBS -L 1 zsh -c '
  ls=$1 cs=$2 sm=$3
  k="ls${ls}-cs${cs}-sm${sm}"
  if .venv/bin/python pipeline.py -i source/ -o variants/$k '"$BASE"' --label-sigma $ls --curve-sigma $cs --smoothing $sm > variants/$k.log 2>&1; then
    echo "ok $k"
  else
    echo "FAIL $k (see variants/$k.log)"
  fi
' _

.venv/bin/python variants/make_compare.py && echo "wrote variants/compare.html"
