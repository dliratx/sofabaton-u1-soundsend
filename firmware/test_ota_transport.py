"""Exercise uploader framing/ack handling against original ARM OTA handlers.

GATT is modeled. These tests cannot verify Windows BLE or radio transport.
"""
import asyncio,hashlib,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from test_ota_lab_arm import OtaMachine
from build_ota_lab import PACKAGE,parse_package

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from u1_ota_install import Channel,CMD,DATA,ERROR,validated_package,finish_commit_link


class GattModel:
    def __init__(self):
        self.machine=OtaMachine();self.machine.initialize_transfer()
        self.chars={u:SimpleNamespace(uuid=u,properties=props) for u,props in (
            (CMD,['write','indicate']),(DATA,['notify','write-without-response']),(ERROR,['notify']))}
        self.services=SimpleNamespace(get_service=lambda _:SimpleNamespace(get_characteristic=lambda u:self.chars.get(u)))
        self.notify={};self.sent=[];self.is_connected=True;self.bad_ack=False
    async def start_notify(self,char,callback):self.notify[char.uuid]=callback
    async def stop_notify(self,char):del self.notify[char.uuid]
    async def write_gatt_char(self,char,packet,response):
        packet=bytes(packet);self.sent.append((char.uuid,packet,response))
        reply=self.machine.request(0x2A0C if char.uuid==CMD else 0x2B28,packet)
        if self.bad_ack and char.uuid==DATA:reply=reply[:3]+b'\x03'
        self.notify[char.uuid](char,reply)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_postboot_verification_is_read_only_and_checks_actual_defaults(self):
        from u1_delta_install import verify_result_dispatch
        plan={'result_poll_dispatch':'lab','result_default_enabled':True,'result_lab_revision':17,'result_lab_build_id':'0x544d5248'}
        states={1:{'enabled':True,'phase':'idle','lab_protocol_revision':17,'tx_mode':10},
                5:{'build_id':'0x544d5248'},7:{'rx_clear_mode':2,'tx_lead_us':72}}
        async def read(client,command):return states[command]
        with patch('u1_delta_install.exchange',new=AsyncMock(side_effect=read)) as exchange:
            result=await verify_result_dispatch(object(),plan);self.assertTrue(result['enabled'])
            self.assertEqual([c.args[1] for c in exchange.await_args_list],[1,5,7])
            states[7]['tx_lead_us']=0
            with self.assertRaises(RuntimeError):await verify_result_dispatch(object(),plan)
            states[7]['tx_lead_us']=72;states[5]['build_id']='0x544d5247'
            with self.assertRaises(RuntimeError):await verify_result_dispatch(object(),plan)
    async def test_transfer_precheck_still_requires_disabled_idle_firmware(self):
        from u1_delta_install import verify_dispatch
        for state in ({'enabled':True,'phase':'idle'},{'enabled':False,'phase':'active'}):
            with patch('u1_delta_install.exchange',new=AsyncMock(return_value=state)):
                with self.assertRaises(RuntimeError):await verify_dispatch(object(),'lab')
    async def test_host_data_packets_reach_original_flash_page_handler(self):
        client=GattModel();channel=Channel(client,lambda *a,**k:None);await channel.start()
        application=parse_package(PACKAGE.read_bytes())
        for address in range(0,128,16):await channel.send_data(address,application[address:address+16])
        self.assertEqual(bytes(client.machine.m.mem_read(0x20000,128)),application[:128])
        self.assertEqual(client.machine.flash_writes,[('program',0x20000,128)])
        self.assertTrue(all(response is False for _,_,response in client.sent))
        await channel.stop();self.assertEqual(client.notify,{})
    async def test_error_ack_stops_without_retry_or_commit(self):
        client=GattModel();client.bad_ack=True
        channel=Channel(client,lambda *a,**k:None);await channel.start()
        with self.assertRaises(RuntimeError):await channel.send_data(0,bytes(16))
        self.assertEqual(len(client.sent),1);self.assertEqual(client.sent[0][0],DATA)
        self.assertEqual(client.machine.flash_writes,[])
    async def test_control_rejection_is_not_accepted_as_success(self):
        client=GattModel();client.machine.m.mem_write(0x2000036A,b'\0')
        channel=Channel(client,lambda *a,**k:None);await channel.start()
        with self.assertRaises(RuntimeError):await channel.command(4)
        self.assertEqual(len(client.sent),1)
    async def test_exact_package_hash_is_required_before_any_ble_work(self):
        with self.assertRaises(ValueError):validated_package('0'*64)
    async def test_commit_waits_for_dispatch_and_does_not_disconnect_an_already_closed_link(self):
        client=SimpleNamespace(is_connected=True);command=AsyncMock();events=[]
        async def settle(seconds):
            self.assertEqual(seconds,1);client.is_connected=False
        with patch('u1_ota_install.asyncio.sleep',side_effect=settle):
            await finish_commit_link(SimpleNamespace(client=client,command=command),lambda event,**kw:events.append(event))
        command.assert_not_awaited();self.assertIn('commit_link_already_disconnected',events)
    async def test_commit_requests_device_disconnect_once_if_link_remains_after_dispatch(self):
        command=AsyncMock();events=[]
        with patch('u1_ota_install.asyncio.sleep',new_callable=AsyncMock) as settle:
            await finish_commit_link(SimpleNamespace(client=SimpleNamespace(is_connected=True),command=command),lambda event,**kw:events.append(event))
        settle.assert_awaited_once_with(1);command.assert_awaited_once_with(6)
        self.assertIn('commit_link_termination_acknowledged',events)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TransportTests))
    report={'tests_run':result.testsRun,'passed':result.wasSuccessful(),
            'package_sha256':hashlib.sha256(PACKAGE.read_bytes()).hexdigest(),
            'scope':'Host OTA framing, strict acknowledgements and error stopping against original V32 ARM handlers, with modeled GATT. No real adapter or device.'}
    (ROOT/'ota-transport-test-results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
