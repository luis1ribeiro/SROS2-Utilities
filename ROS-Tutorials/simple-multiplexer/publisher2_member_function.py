# Copyright 2016 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import rclpy, random
from rclpy.node import Node

from std_msgs.msg import String

# turn - rigth/left move - forward/back
global turn
turn = 'rl'
global move
move = 'fb'

# MinimalPublisher is a subclass of Node
class MinimalPublisher(Node):

    def __init__(self):
        super().__init__('minimal_publisher')
        # create_publisher declares that the node publishes messages of type String (imported from the std_msgs.msg module), over a topic named topic, and that the “queue size” is 10.
        # Queue size is a required QoS (quality of service) setting that limits the amount of queued messages if a subscriber is not receiving them fast enough.
        self.publisher_ = self.create_publisher(String, 'topic_2', 10)
        timer_period = 3  # seconds
        self.timer = self.create_timer(timer_period, self.timer_callback)
        self.id = 2
        self.t = random.choice(turn)
        self.m = random.choice(move)

    # timer_callback creates a message with the counter value appended, and publishes it to the console with get_logger().info.
    def timer_callback(self):
        msg = String()
        msg.data  = f'{self.id} :: Turn {self.t} and Go {self.m}'
        self.publisher_.publish(msg)
        self.get_logger().info('Publishing on 2nd: "%s"' % msg.data)
        self.random_turn_move()

    def random_turn_move(self):
        self.t = random.choice(turn)
        self.m = random.choice(move)

# First the rclpy library is initialized, then the node is created, and then it “spins” the node so its callbacks are called.
def main(args=None):
    rclpy.init(args=args)

    minimal_publisher = MinimalPublisher()

    rclpy.spin(minimal_publisher)

    # Destroy the node explicitly
    # (optional - otherwise it will be done automatically
    # when the garbage collector destroys the node object)
    minimal_publisher.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()

