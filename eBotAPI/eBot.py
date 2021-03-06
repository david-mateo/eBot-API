from time import sleep
import os
import sys
from math import degrees, pi
import glob
import serial
from threading import Lock, Thread
from .Locator_EKF import Locator_EKF

if os.name == 'nt':
    try:
        import _winreg as winreg
    except ImportError:
        pass


class SafeSerial(serial.Serial):
    def __init__(self, *args, **kws):
        lock = kws.pop("lock", None)
        if isinstance(lock, type(Lock())):
            self.lock = lock
        else:
            self.lock = Lock()
        super(SafeSerial, self).__init__(*args, **kws)
        return

    def readline(self):
        with self.lock:
            m = super(SafeSerial, self).readline()
        return m.decode()

    def write(self, mess, **kws):
        if not isinstance(mess, bytes):
            mess = mess.encode()
        with self.lock:
            m = super(SafeSerial, self).write(mess, **kws)
        return m

    def flushInput(self):
        with self.lock:
            m = super(SafeSerial, self).flushInput()
        return m

    def flushOutput(self):
        with self.lock:
            m = super(SafeSerial, self).flushOutput()
        return m


class eBot:
    def __init__(self, pos=(0., 0.), heading=0., lock=None):
        self.sonarValues = [0, 0, 0, 0, 0, 0]
        self.all_Values = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        self.port = None
        self.serialReady = False
        self.ldrvalue = [0, 0]
        self.p_value = [0, 0]
        self.acc_values = [0, 0, 0, 0, 0, 0]
        self.pos_values = [0, 0, 0]
        self.EKF = Locator_EKF(pos, heading, 0.1)
        self.updating = False
        self.offset = False
        self.gyro_heading = degrees(heading)
        self.offset_counter_iteration = 100
        self.lock = lock
        return

    def destroy(self):
        """
        Destructor function for eBot class.
        """
        self.disconnect()
        self.sonarValues = None
        self.port = None
        self.serialReady = None

    def getOpenPorts(self):
        """
        Windows only function: Obtains a list of tuples with eBot-relevant port
        number and description.

        :rtype: list
        :return: devicePorts: list of port numbers and descriptions of relevant
                              serial devices.
        """
        path = 'HARDWARE\\DEVICEMAP\\SERIALCOMM'
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        ports = []
        # maximum 256 entries, will break anyways
        for i in range(256):
            try:
                val = winreg.EnumValue(key, i)
                port = (str(val[1]), str(val[0]))
                ports.append(port)
            except Exception:
                winreg.CloseKey(key)
                break
        devicePorts = []
        for port in ports:
            # Just because it is formatted that way...
            if 'BthModem' in port[1][8:] or 'VCP' in port[1][8:] or \
               'ProlificSerial' in port[1][8:]:
                devicePorts.append((int(port[0][3:]) - 1))
        return devicePorts

    def open(self):
        """
        Opens connection with the eBot via BLE. Connects with the first eBot
        that the computer is paired to.

        :raise Exception: No eBot found
        """
        self.connect()

    def connect(self, port_path=None):
        """
        Opens connection with the eBot via BLE. Connects with the first eBot
        that the computer is paired to.

        :raise Exception: No eBot found
        """
        baudRate = 115200
        if port_path:
            ports = [port_path]
        else:
            ports = []
            if os.name == "posix":
                if sys.platform == "linux2":
                    ports = glob.glob('/dev/rfcomm*')
                elif sys.platform == "darwin":
                    ports = glob.glob('/dev/tty.eBo*')
                else:
                    sys.stderr.write("Unknown posix OS.")
                    sys.exit()
            elif os.name == "nt":
                ports = self.getOpenPorts()

        connect = 0
        line = "a"
        print("# Connecting", end="")
        for port in ports:
            print(".", end="")
            try:
                if (line[:2] == "eB"):
                    break
                s = SafeSerial(port, baudRate, timeout=5.0, writeTimeout=5.0,
                               lock=self.lock)
                s.flushInput()
                s.flushOutput()
                strikes = 40
                while line[:2] != "eB" and strikes > 0:
                    strikes -= 1
                    s.write("<<1?")
                    sleep(0.5)
                    line = s.readline()
                if line[:2] == "eB":
                    connect = 1
                    self.port = s
                    self.portName = port
                    self.port.flushInput()
                    self.port.flushOutput()
                else:
                    s.close()
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass

        if (connect == 0):
            try:
                s.close()
            except Exception:
                pass
            raise Exception("No eBot found")

        try:
            self.port.write('<<1E')
            sleep(0.4)
            line = self.port.readline()
            if line != ">>1B\n" and line != ">>1B" and line != ">>\n" and \
               line != ">>":
                self.lostConnection()
            self.port.write("<<1O")
            sleep(0.4)
            self.port.write("F")
            sleep(0.2)
            self.port.flushInput()
            self.port.flushOutput()
            print("Done")
            self.serialReady = True
        except Exception:
            sys.stderr.write("Could not write to serial port.\n")
            self.serialReady = False
            sys.stderr.write("Robot turned off or no longer connected.\n")
        self.start_update_background()
        return

    def start_update_background(self):
        if not self.updating:
            self.update_thread = Thread(target=self.update_background)
            self.updating = True
            self.update_thread.start()
            print("# Turning on localization procedure. Computing offset",
                  end=" ")
            while not self.offset:
                sleep(0.5)
                print(". ", end=" ")
            print("Done")
        return

    def stop_update_background(self):
        self.updating = False
        if self.update_thread.is_alive():
            self.update_thread.join(5)
        if self.update_thread.is_alive():
            raise Exception("Could not stop update_background thread properly")
        return

    def read_all(self):
        # Uncomment this if request-based data
        # transfer is implemented
        # if self.serialReady:
        #    try:
        #       self.port.write("2S")
        #    except:
        #        self.lostConnection()
        # line = self.port.readline()
        line = None
        while self.port.inWaiting() > 90:  # one message is 105 chars long
            line = self.port.readline()
        if line:
            try:
                data = [float(x) for x in line.rstrip('\n').split(";")]
            except Exception:
                sys.stderr.write("Bad format message:")
                sys.stderr.write(line)
                data = []
        else:
            data = []
        return data

    def set_offset(self):
        if not self.offset:
            data = []
            while not data:
                data = self.read_all()
            self.Ax_offset = data[1]
            self.Ay_offset = data[2]
            self.Az_offset = data[3]
            self.Gx_offset = data[4]
            self.Gy_offset = data[5]
            self.Gz_offset = data[6]
            for i in range(self.offset_counter_iteration):
                data = []
                while not data:
                    data = self.read_all()
                self.Ax_offset += data[1]
                self.Ay_offset += data[2]
                self.Az_offset += data[3]
                self.Gx_offset += data[4]
                self.Gy_offset += data[5]
                self.Gz_offset += data[6]
            self.Ax_offset /= self.offset_counter_iteration
            self.Ay_offset /= self.offset_counter_iteration
            self.Az_offset /= self.offset_counter_iteration
            self.Gx_offset /= self.offset_counter_iteration
            self.Gy_offset /= self.offset_counter_iteration
            self.Gz_offset /= self.offset_counter_iteration
            self.time_stamp = data[0]
            self.offset = True
        return

    def unset_offset(self):
        if self.offset:
            self.Ax_offset = None
            self.Ay_offset = None
            self.Az_offset = None
            self.Gx_offset = None
            self.Gy_offset = None
            self.Gz_offset = None
            self.offset = False
        return

    def update_all(self):
        data = self.read_all()
        if data:
            self.prev_time_stamp = self.time_stamp
            self.time_stamp, self.Ax, self.Ay, self.Az, self.Gx, self.Gy, \
                self.Gz, self.Ultrasonic_rear_right, self.Ultrasonic_right, \
                self.Ultrasonic_front, self.Ultrasonic_left, \
                self.Ultrasonic_rear_left, self.Ultrasonic_back, \
                self.encoder_right, self.encoder_left, self.LDR_top, \
                self.LDR_front, self.temperature_sensor, self.voltage, \
                self.current = data
        else:
            return data
        sampling_time = (self.time_stamp - self.prev_time_stamp) / 1000.
        if sampling_time > 0:
            if abs(self.Gz - self.Gz_offset) > 50:  # to remove the noise
                # the integration to get the heading
                delta = (self.Gz - self.Gz_offset) / 130.5
                self.gyro_heading += sampling_time * delta
            heading_scaled = self.gyro_heading % 360.
            if heading_scaled > 180:
                heading_scaled -= 360
            elif heading_scaled < -180:
                heading_scaled += 360
            self.pos_values[0], self.pos_values[1], self.pos_values[2] = \
                self.EKF.update_state([heading_scaled * pi / 180.,
                                       self.encoder_right / 1000.,
                                       self.encoder_left / 1000.],
                                      sampling_time)
            self.pos_values[2] = degrees(self.pos_values[2])
        return data

    def update_background(self):
        self.set_offset()
        while self.updating:
            # If update_all produces an error, the loop will end cleanly but
            # the pos_values will be erased, so that any thread trying to
            # access that will raise an Exception (or get nonsense).
            try:
                self.update_all()
            except Exception as ex:
                self.pos_values = None
                self.updating = False
                raise ex
        self.halt()
        return

    def close(self):
        """
        Close BLE connection with eBot.
        """
        self.disconnect()

    # TODO: add disconnect feedback to robot
    def disconnect(self):
        """
        Close BLE connection with eBot.
        """
        self.stop_update_background()
        if self.serialReady:
            try:
                self.port.close()
            except Exception:
                self.lostConnection()

    def robot_uS(self):
        """
        Retrieves and returns all six ultrasonic sensor values in meters.

        :rtype: list
        :return: sonarValues
        """
        self.sonarValues[0] = float(self.Ultrasonic_rear_left) / 1000
        self.sonarValues[1] = float(self.Ultrasonic_left) / 1000
        self.sonarValues[2] = float(self.Ultrasonic_front) / 1000
        self.sonarValues[3] = float(self.Ultrasonic_right) / 1000
        self.sonarValues[4] = float(self.Ultrasonic_rear_right) / 1000
        self.sonarValues[5] = float(self.Ultrasonic_back) / 1000
        return self.sonarValues

    def calibration_values(self):
        """
        Retrieves and returns the calibration values of the eBot.

        :rtype: list
        :return: all_Values (calibration values)
        """
        if self.serialReady:
            try:
                self.port.write("2C")
            except Exception:
                self.lostConnection()
        line = self.port.readline()
        values = line.split(";")
        while len(values) < 10:
            if self.serialReady:
                try:
                    self.port.write("2C")
                except Exception:
                    self.lostConnection()
            line = self.port.readline()
            values = line.split(";")
        self.all_Values[0] = float(values[0])
        self.all_Values[1] = float(values[1])
        self.all_Values[2] = float(values[2])
        self.all_Values[3] = float(values[3])
        self.all_Values[8] = float(values[4]) / 1000
        self.all_Values[7] = float(values[5]) / 1000
        self.all_Values[6] = float(values[6]) / 1000
        self.all_Values[5] = float(values[7]) / 1000
        self.all_Values[4] = float(values[8]) / 1000
        self.all_Values[9] = float(values[9]) / 1000
        return self.all_Values

    def halt(self):
        """
        Halts the eBot, turns the motors and LEDs off.
        """
        if self.serialReady:
            try:
                self.port.write("2H")
            except Exception:
                self.lostConnection()
        sleep(0.05)

    def led(self, bool):
        """
        Controls the state of the LED on the eBot.

        :param bool: Defines whether the LED should turn ON (1) or OFF (0)
        """
        if (bool == 1):
            self.led_on()
        elif (bool == 0):
            self.led_off()
        else:
            self.led_off()
        sleep(0.05)

    def led_on(self):
        """
        Turns the LED on the eBot ON.
        """
        if self.serialReady:
            try:
                self.port.write("2L")
            except Exception:
                self.lostConnection()
        sleep(0.05)

    def led_off(self):
        """
        Turns the LED on the eBot OFF.
        """
        if self.serialReady:
            try:
                self.port.write("2l")
            except Exception:
                self.lostConnection()
        sleep(0.05)

    def light(self):
        """
        Retrieves and returns a list of tuples with the light index. 0 index is
        front and 1st index is top LDR readings.

        :rtype: list
        :return: ldrvalue: LDR Readings
        """
        self.ldrvalue[0] = float(self.LDR_front)
        self.ldrvalue[1] = float(self.LDR_top)
        return self.ldrvalue

    # Double check true vs. false
    def obstacle(self):
        """
        Tells whether or not there is an obstacle less than 250 mm away from
        the front of the eBot.

        :rtype: bool
        :return: True if obstacle exists
        """
        return self.Ultrasonic_front <= 250

    # TODO: implement x, y, z returns and a seperate odometry function
    def acceleration(self):
        """
        Retrieves and returns accelerometer values; absolute values of X,Y and
        theta coordinates of robot with reference
        to starting position.

        :rtype: list
        :return: acc_values: Accelerometer values
        """
        self.acc_values[0] = float(self.Ax - self.Ax_offset)
        self.acc_values[1] = float(self.Ay - self.Ay_offset)
        self.acc_values[2] = float(self.Az - self.Az_offset)
        self.acc_values[3] = float(self.Gx - self.Gx_offset)
        self.acc_values[4] = float(self.Gy - self.Gy_offset)
        self.acc_values[5] = float(self.Gz - self.Gz_offset)
        return self.acc_values

    def position(self):
        """
        Retrieves and returns position values of the eBot.

        :rtype: list
        :return: pos_values: X,Y position values + heading
        """
        return self.pos_values

    # TODO: implement temperature feedback from MPU6050 IC
    def temperature(self):
        """
        Retrieves and returns temperature reading from the eBot.

        :rtype: int
        :return: Temperature value.
        """

        return int(self.temperature_sensor)

    def power(self):
        """

        :return:
        """
        self.p_value[0] = float(self.voltage)
        self.p_value[1] = float(self.current)
        return self.p_value

    def imperial_march(self):
        """

        """
        if self.serialReady:
            try:
                self.port.write("2b")
            except Exception:
                self.lostConnection()

    def buzzer(self, btime, bfreq):
        """
        Plays the buzzer for given time at given frequency.

        :param btime: Time in Seconds
        :param bfreq: Frequency in Hertz
        """
        buzzer_time = int(btime)
        buzzer_frequency = int(bfreq)
        bt1 = str(buzzer_time)
        bf1 = str(buzzer_frequency)
        str_len = len(bt1) + len(bf1) + 2
        str_len = str_len + 48
        myvalue = chr(str_len) + 'B' + bt1 + ';' + bf1
        if self.serialReady:
            try:
                self.port.write(myvalue)
            except Exception:
                self.lostConnection()
        return

    def port_name(self):
        """
        Returns port name of currently connected eBot.

        :return: port: Port name
        """
        return self.port

    def port_close(self):
        """
        Closes the COM port that corresponds to the eBot object.

        :raise Exception: Could not close COM port
        """
        try:
            self.port.close()
        except Exception:
            self.serialReady = False
            raise Exception("Could not close COM port.")

    # TODO: Add com port argument functionality
    def port_open(self):
        """
        Still under development, currently just calls connect
        """
        self.connect()

    def wheels(self, LS, RS):
        """
        Controls the speed of the wheels of the robot.
        :param LS: Speed of left motor
        :param RS: Speed of right motor
        """
        if LS > 1:
            LS = 1
        elif LS < -1:
            LS = -1
        if RS > 1:
            RS = 1
        elif RS < -1:
            RS = -1
        left_speed = int((LS + 2) * 100)
        right_speed = int((RS + 2) * 100)
        try:
            self.port.write("8w{:d};{:d}".format(left_speed, right_speed))
        except Exception:
            self.lostConnection()
        sleep(0.05)
        return

    def calibration(self, LS, RS):
        """
        Calibrates the wheels of the robot.
        :param LS: Speed of left motor
        :param RS: Speed of right motor
        """
        if LS > 9999:
            LS = 9999
        elif LS < 1:
            LS = 1
        if RS > 9999:
            RS = 9999
        elif RS < 1:
            RS = 1
        left_calibration = str(LS).zfill(4)
        right_calibration = str(RS).zfill(4)
        try:
            self.port.write(":c{:s};{:s}".format(left_calibration,
                                                 right_calibration))
        except Exception:
            self.lostConnection()
        sleep(0.05)
        return

    def lostConnection(self):
        """
        Handler for the case that the computer loses connection with the eBot.

        :raise Exception: Robot Connection Lost
        """
        try:
            self.port.close()
        except Exception:
            pass
        self.serialReady = False
        raise Exception("Robot Connection Lost")
