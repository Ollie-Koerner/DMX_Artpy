from array import array
from ola.ClientWrapper import ClientWrapper

class DMXData(array):
    def tostring(self):
        return self.tobytes()

wrapper = ClientWrapper()
client = wrapper.Client()

dmx = DMXData('B', [255] + [0] * 511)

def sent(state):
    print("Status:", state.Succeeded())
    print("Status object:", state)
    wrapper.Stop()

client.SendDmx(0, dmx, sent)
wrapper.Run()