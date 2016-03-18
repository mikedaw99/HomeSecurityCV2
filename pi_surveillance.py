# USAGE
# python pi_surveillance.py --conf conf.json

# import the necessary packages
from pyimagesearch.tempimage import TempImage
from dropbox.client import DropboxOAuth2FlowNoRedirect
from dropbox.client import DropboxClient
from picamera.array import PiRGBArray
from picamera import PiCamera
import argparse
import warnings
import datetime
import imutils
import json
import time
import cv2
import logging
import logging.handlers

def delete_files(api_client, logger, the_path):
    #the path may not exist yet
    try:
        resp = api_client.metadata(the_path)
        if 'contents' in resp:
            for f in resp['contents']:
                name = os.path.basename(f['path'])
                encoding = locale.getdefaultlocale()[1] or 'ascii'
                file_name=('%s' % name).encode(encoding)
                #logger.info(file_name)    
                #Delete this record.
                api_client.file_delete(the_path + "/" + file_name)
                logger.info("The file %s was deleted" % file_name)
                #print "The file {} was deleted".format(file_name)
    except Exception as e:
        logger.exception("Failed to delete old files on %",the_path)   

def main():
    # create logger'
    logger = logging.getLogger('home_security')
    logger.setLevel(logging.DEBUG)
    # create file handler which logs even debug messages
    fh = logging.FileHandler('home_security.log')
    fh.setLevel(logging.DEBUG)
    # create console handler with a higher log level
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    # create formatter and add it to the handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)
    # add the handlers to the logger
    logger.addHandler(fh)
    logger.addHandler(ch)
        
    #syslog = logging.handlers.SysLogHandler(address = '/dev/log')   
    #syslog.setLevel(logging.ERROR)
        #logger.addHandler(syslog)
    
    # construct the argument parser and parse the arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--conf", required=True,
        help="path to the JSON configuration file")
    args = vars(ap.parse_args())

    # filter warnings, load the configuration and initialize the Dropbox
    # client
    warnings.filterwarnings("ignore")
    conf = json.load(open(args["conf"]))
    client = None

    # check to see if the Dropbox should be used
    if conf["use_dropbox"]:
        if conf["accessToken"]:
            accessToken=conf["accessToken"]
            userID="mikedaw99@gmail.com"
        else:
            # connect to dropbox and start the session authorization process
            flow = DropboxOAuth2FlowNoRedirect(conf["dropbox_key"], conf["dropbox_secret"])
            print "[INFO] Authorize this application: {}".format(flow.start())
            authCode = raw_input("Enter auth code here: ").strip()

            # finish the authorization and grab the Dropbox client
            (accessToken, userID) = flow.finish(authCode)

        print "accessToken:{} userID:{}".format(accessToken,userID)
        client = DropboxClient(accessToken)
        print "[SUCCESS] dropbox account linked"
        

    # initialize the camera and grab a reference to the raw camera capture
    camera = PiCamera()
    camera.resolution = tuple(conf["resolution"])
    camera.framerate = conf["fps"]
    rawCapture = PiRGBArray(camera, size=tuple(conf["resolution"]))

    # allow the camera to warmup, then initialize the average frame, last
    # uploaded timestamp, and frame motion counter
    print "[INFO] warming up..."
    time.sleep(conf["camera_warmup_time"])
    avg = None
    lastUploaded = datetime.datetime.now()
    dayNumber=lastUploaded.toordinal()
    motionCounter = 0

    # capture frames from the camera
    for f in camera.capture_continuous(rawCapture, format="bgr", use_video_port=True):
        # grab the raw NumPy array representing the image and initialize
        # the timestamp and movement flag
        frame = f.array
        timestamp = datetime.datetime.now()
        dayNumberNow = timestamp.toordinal()
        movement = False

        # resize the frame, convert it to grayscale, and blur it
        frame = imutils.resize(frame, width=500)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        # if the average frame is None, initialize it
        if avg is None:
            print "[INFO] starting background model..."
            avg = gray.copy().astype("float")
            rawCapture.truncate(0)
            continue

        # accumulate the weighted average between the current frame and
        # previous frames, then compute the difference between the current
        # frame and running average
        cv2.accumulateWeighted(gray, avg, 0.5)
        frameDelta = cv2.absdiff(gray, cv2.convertScaleAbs(avg))

        # threshold the delta image, dilate the thresholded image to fill
        # in holes, then find contours on thresholded image
        thresh = cv2.threshold(frameDelta, conf["delta_thresh"], 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)

        (_, cnts, _) = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # loop over the contours
        x,y = 0,0
        for c in cnts:
            (x, y, w, h) = cv2.boundingRect(c)
            # if the contour is too small, y co-ord is too high ignore it
            if (cv2.contourArea(c) < conf["min_area"]) or (y > 230):
                continue

            # compute the bounding box for the contour, draw it on the frame,
            # and update the text
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            movement = True

        # draw the text and timestamp on the frame
        ts = timestamp.strftime("%A %d %B %Y %I:%M:%S%p")
        cv2.putText(frame, "x: {} y: {}".format(x,y), (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)
        cv2.putText(frame, ts, (10, frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX,
            0.35, (0, 0, 255), 1)

        # check to see if there is movement
        if movement:
            logger.info("movement detected")
            # check to see if enough time has passed between uploads
            if (timestamp - lastUploaded).seconds >= conf["min_upload_seconds"]:
                # increment the motion counter
                motionCounter += 1
                # check to see if the number of frames with consistent motion is
                # high enough
                if motionCounter >= conf["min_motion_frames"]:
                    # check to see if dropbox should be used
                    if conf["use_dropbox"]:
                        # write the image to temporary file
                        t = TempImage()
                        cv2.imwrite(t.path, frame)
                        suffix=(dayNumberNow % 20)+1 #(1..20)
                        new_path="Public/SecurityDawson65_" + str(suffix)
                        
                        if dayNumber != dayNumberNow:
                            #midnight. clear new folder
                            delete_files(client, logger, new_path)
                            dayNumber = dayNumberNow
                            
                        # upload the image to Dropbox and cleanup the tempory image
                        try:
                            path = "{base_path}/{timestamp}.jpg".format(base_path=new_path, timestamp=ts)
                            logger.info("[UPLOAD] {}".format(path))
                            client.put_file(path, open(t.path, "rb"))
                            t.cleanup()
                        except Exception as e:
                            logger.exception("Network error. Upload failed")
                            time.sleep(30) #wait for dropbox to recover
                            
                    # update the last uploaded timestamp and reset the motion
                    # counter
                    lastUploaded = timestamp
                    motionCounter = 0
                else:
                    logger.info("failed min_motion_frames {}".format(motionCounter))
            else:
                logger.info("failed min_upload_seconds")
                
        # otherwise, no movement detected
        else:
            motionCounter = 0
            
        # check to see if the frames should be displayed to screen
        if conf["show_video"]:
            # display the security feed
            cv2.imshow("Security Feed", frame)
            key = cv2.waitKey(1) & 0xFF

            # if the `q` key is pressed, break from the loop
            if key == ord("q"):
                break

        # clear the stream in preparation for the next frame
        rawCapture.truncate(0)   

if __name__ == '__main__':
    main()
