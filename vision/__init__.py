"""画面识别：从 OB / 回放画面里读出时钟、比分条和底部面板的数字，交给状态中心。

只用 numpy 做图像运算。不要在这个包里 import cv2：实时采集用 PyAV 解码，
它和 opencv-python 各带一份 FFmpeg，放进同一个进程会有 Objective-C 类重名的警告。
读写图片文件的活放在 tools/ 里做。
"""
