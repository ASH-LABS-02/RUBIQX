#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <string>
#include <thread>

#include <opencv2/opencv.hpp>

#include "System.h"

// Ctrl+C changes this flag instead of immediately killing the process.
std::atomic<bool> stop_requested(false);

void handle_signal(int)
{
    stop_requested.store(true);
}

int main(int argc, char** argv)
{
    if (argc != 4)
    {
        std::cerr
            << "Usage:\n"
            << argv[0]
            << " vocabulary_file settings_file camera_url\n\n"
            << "Example:\n"
            << argv[0]
            << " Vocabulary/ORBvoc.txt"
            << " Examples/Monocular/LaptopCamera.yaml"
            << " http://172.x.x.x:5000/video\n";

        return 1;
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    const std::string vocabulary_file = argv[1];
    const std::string settings_file = argv[2];
    const std::string camera_url = argv[3];

    std::cout << "Opening camera stream:\n"
              << camera_url << "\n";

    cv::VideoCapture camera(camera_url, cv::CAP_FFMPEG);

    if (!camera.isOpened())
    {
        std::cerr
            << "ERROR: Could not open camera stream.\n"
            << "Confirm that camera_server.py is running and that "
            << "the URL is reachable.\n";

        return 1;
    }

    // Try to minimize old buffered frames.
    camera.set(cv::CAP_PROP_BUFFERSIZE, 1);

    ORB_SLAM3::System slam(
        vocabulary_file,
        settings_file,
        ORB_SLAM3::System::MONOCULAR,
        true
    );

    std::cout << "\nLive camera connected.\n";
    std::cout << "Stop with Q, Esc, or Ctrl+C.\n";
    std::cout << "Ctrl+C will now request a graceful shutdown.\n\n";

    const auto start_time = std::chrono::steady_clock::now();

    int consecutive_failed_frames = 0;
    int valid_tracking_frames = 0;
    int total_frames = 0;

    const int maximum_failed_frames = 50;
    const int minimum_tracked_frames_to_save = 10;

    while (!stop_requested.load())
    {
        cv::Mat frame;

        const bool frame_received = camera.read(frame);

        if (!frame_received || frame.empty())
        {
            consecutive_failed_frames++;

            std::cerr
                << "Frame unavailable: "
                << consecutive_failed_frames
                << "/"
                << maximum_failed_frames
                << "\n";

            if (consecutive_failed_frames >= maximum_failed_frames)
            {
                std::cerr
                    << "Camera stream unavailable for too long. "
                    << "Stopping cleanly.\n";

                break;
            }

            std::this_thread::sleep_for(
                std::chrono::milliseconds(100)
            );

            continue;
        }

        consecutive_failed_frames = 0;
        total_frames++;

        const auto current_time =
            std::chrono::steady_clock::now();

        const double timestamp =
            std::chrono::duration<double>(
                current_time - start_time
            ).count();

        slam.TrackMonocular(frame, timestamp);

        // ORB-SLAM3 tracking state 2 means tracking is OK.
        const int tracking_state = slam.GetTrackingState();

        if (tracking_state == 2)
        {
            valid_tracking_frames++;
        }

        cv::Mat display = frame.clone();

        std::string status_text;

        if (tracking_state == 2)
        {
            status_text = "TRACKING OK";
        }
        else if (tracking_state == 1)
        {
            status_text = "INITIALIZING";
        }
        else if (tracking_state == 3)
        {
            status_text = "RECENTLY LOST";
        }
        else if (tracking_state == 4)
        {
            status_text = "TRACKING LOST";
        }
        else
        {
            status_text =
                "TRACKING STATE: "
                + std::to_string(tracking_state);
        }

        const cv::Scalar status_color =
            tracking_state == 2
                ? cv::Scalar(0, 255, 0)
                : cv::Scalar(0, 0, 255);

        cv::rectangle(
            display,
            cv::Point(0, 0),
            cv::Point(display.cols, 72),
            cv::Scalar(0, 0, 0),
            cv::FILLED
        );

        cv::putText(
            display,
            status_text,
            cv::Point(15, 30),
            cv::FONT_HERSHEY_SIMPLEX,
            0.75,
            status_color,
            2,
            cv::LINE_AA
        );

        cv::putText(
            display,
            "Q / Esc / Ctrl+C: stop and save",
            cv::Point(15, 60),
            cv::FONT_HERSHEY_SIMPLEX,
            0.55,
            cv::Scalar(255, 255, 255),
            1,
            cv::LINE_AA
        );

        cv::imshow("Live camera input", display);

        const int key = cv::waitKey(1) & 0xff;

        if (key == 'q' || key == 'Q' || key == 27)
        {
            std::cout
                << "Stop requested from camera window.\n";

            stop_requested.store(true);
        }
    }

    std::cout << "\nStopping camera input...\n";

    camera.release();
    cv::destroyAllWindows();

    std::cout << "Shutting down ORB-SLAM3...\n";
    slam.Shutdown();

    std::cout
        << "Frames received: "
        << total_frames
        << "\n";

    std::cout
        << "Frames with valid tracking: "
        << valid_tracking_frames
        << "\n";

    if (valid_tracking_frames >= minimum_tracked_frames_to_save)
    {
        std::cout
            << "Saving keyframe trajectory to "
            << "LiveKeyFrameTrajectory.txt ...\n";

        slam.SaveKeyFrameTrajectoryTUM(
            "LiveKeyFrameTrajectory.txt"
        );

        std::cout
            << "Trajectory saved successfully.\n";
    }
    else
    {
        std::cout
            << "Trajectory not saved because valid tracking "
            << "was not established for enough frames.\n";

        std::cout
            << "Point the camera at a static textured scene "
            << "and move it slowly sideways.\n";
    }

    std::cout << "Finished.\n";

    return 0;
}
