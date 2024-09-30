#!/usr/bin/env bash

set -x
set -e

# Check if there is apptimetracker_run.sh in dir
if [ ! -f "$PWD/timetracker_run.sh" ]; then
    echo "File timetracker_run.sh not found in $PWD"
    exit 1
fi

sudo chmod +x "$PWD/timetracker_run.sh"

cp apptimetracker.service.templ apptimetracker.service

sed -i "s:{USER}:${USER}:g" apptimetracker.service
sed -i "s:{PWD}:${PWD}:g" apptimetracker.service
sudo cp apptimetracker.service /etc/systemd/system/apptimetracker.service


sudo systemctl daemon-reload
sudo systemctl enable apptimetracker.service
sudo systemctl start apptimetracker.service
