# SIH Search-and-Rescue Drone SLAM Prototype

This is a laptop-camera proof of concept for GPS-denied visual mapping.
A Windows camera server sends RGB frames to ORB-SLAM3 in Ubuntu/WSL2.
ORB-SLAM3 builds a sparse 3D map. A quadcopter-shaped marker displays
the estimated camera pose; it is not a physical or simulated flying drone.

## Files

- src/camera_server.py: Windows webcam stream
- src/live_mono.cc: live ORB-SLAM3 camera input
- config/LaptopCamera.yaml: provisional camera settings
- patches/orb-slam3-changes.patch: build target and viewer marker changes

## Run

Start the camera server in Windows Command Prompt:

    cd C:\orb-camera
    python camera_server.py

Then, from the ORB-SLAM3 directory in Ubuntu:

    WINDOWS_HOST=$(ip route | awk '/default/ {print $3; exit}')
    ./Examples_old/Stereo-Inertial/live_mono \
      Vocabulary/ORBvoc.txt \
      Examples/Monocular/LaptopCamera.yaml \
      "http://$WINDOWS_HOST:5000/video"

## Limitations

The camera calibration is approximate and monocular scale is unknown.
This prototype does not provide autonomous flight, survivor detection,
thermal sensing, or a dense 3D room model. ORB-SLAM3 and Pangolin are
separate third-party projects and are not included in this repository.
