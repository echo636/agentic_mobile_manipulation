"""CPU regression checks for wrist selection, navigation isolation and six-image MCP."""
import base64
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.oracle_cameras import OracleWristCameras,select_wrist_sensors


class StockWristSelection(unittest.TestCase):
    def sensors(self):
        return {f"robot_random:{arm}_realsense_link:Camera:0":SimpleNamespace(name=arm)
                for arm in ("left","right")}

    def test_both_arms_mapped_in_stable_order_without_robot_name_assumption(self):
        selected=select_wrist_sensors(self.sensors())
        self.assertEqual(list(selected),["left_wrist","right_wrist"])
        self.assertEqual(selected["right_wrist"].name,"right")

    def test_missing_duplicate_or_unexpected_head_camera_rejected(self):
        base=self.sensors()
        invalid=[{next(iter(base)):next(iter(base.values()))},
                 {**base,"robot_random:left_realsense_link:Camera:1":object()},
                 {**base,"robot_random:zed_link:Camera:0":object()}]
        for sensors in invalid:
            with self.subTest(names=list(sensors)),self.assertRaises(RuntimeError):
                select_wrist_sensors(sensors)


class NavigationViewIsolation(unittest.TestCase):
    def test_wrist_pixel_cannot_start_legacy_navigation_grounding(self):
        class Ground:
            def _ground(self,target,**kwargs):
                return ("delegated",target["image_ref"],kwargs)
        class Backend(OracleWristCameras,Ground):
            pass
        b=Backend()
        b.current_frames={"fresh-wrist":{"view":"right_wrist"},
                          "fresh-front":{"view":"front"}}
        with self.assertRaises(SkillError) as caught:
            b._ground({"image_ref":"fresh-wrist","point":[.5,.5]},require_object=False)
        self.assertEqual(caught.exception.code,"invalid_navigation_view")
        self.assertEqual(b._ground({"image_ref":"fresh-front"},require_object=False),
                         ("delegated","fresh-front",{"require_object":False}))

    def test_disabled_wrist_uses_base_sensor_policy(self):
        class Base:
            def _additional_rgb_sensors(self):return {}
        class Backend(OracleWristCameras,Base):pass
        b=Backend();b.wrist_cameras_enabled=False
        self.assertEqual(b._additional_rgb_sensors(),{})
        self.assertFalse(b._wrist_camera_contract()["enabled"])


class SixImageMCP(unittest.IsolatedAsyncioTestCase):
    async def test_surround_and_both_wrist_image_bytes_reach_client(self):
        try:import mcp
        except ImportError:self.skipTest("Optional MCP dependency")
        from manipulation_agent.mcp_server import create_server
        from manipulation_agent.oracle_contracts import tool_specs
        png=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aQAAAAABJRU5ErkJggg==")
        views=("front","back","left","right","left_wrist","right_wrist")
        observation={"observation_mode":"oracle_3d_waypoints","oracle_3d":{"fixture":True},
          "images":[{"image_ref":"test-"+v,"view":v,"mime_type":"image/png",
                     "sha256":hashlib.sha256(png).hexdigest()} for v in views]}
        health=json.dumps({"ready":True,"tools":tool_specs()}).encode()
        def fetch(url):return png if "/image/" in url else health
        with patch("manipulation_agent.mcp_server.fetch",side_effect=fetch),patch(
                "manipulation_agent.mcp_server.rpc",return_value={"ok":True,"observation":observation}):
            server=await create_server("http://127.0.0.1:1", oracle_payload="full")
            blocks=await server.call_tool("initialize",{})
        images=[b for b in blocks if getattr(b,"type",None)=="image"]
        self.assertEqual(len(images),6)
        self.assertTrue(all(base64.b64decode(b.data)==png for b in images))
        labels=[b.text for b in blocks if getattr(b,"type",None)=="text" and b.text.startswith("RGB view=")]
        self.assertEqual(labels,[f"RGB view={v}, image_ref=test-{v}" for v in views])



class WristPrivateGroundingIsolation(unittest.TestCase):
    def test_six_view_capture_only_requests_owner_labels_for_surround(self):
        import tempfile
        from pathlib import Path
        import numpy as np
        import torch
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        views=("front","back","left","right","left_wrist","right_wrist")
        sensors={}
        for view in views:
            packet={"rgb":torch.zeros((8,8,4),dtype=torch.uint8),
                    "depth_linear":torch.ones((8,8))}
            sensors[view]=SimpleNamespace(
                get_obs=lambda packet=packet:(packet,{}),intrinsic_matrix=torch.eye(3))
        owners={view:SimpleNamespace(get_data=lambda:{"data":np.zeros((8,8),dtype=np.int32),
                       "info":{"idToLabels":{"0":"background"}}}) for view in views[:4]}
        with tempfile.TemporaryDirectory() as folder:
            backend=SimpleNamespace(rig={v:sensors[v] for v in views[:4]},
                _rgb_sensors=lambda:sensors,_position_rig=lambda:None,
                _wait_for_rgb_annotations=lambda:None,og=SimpleNamespace(sim=SimpleNamespace(render=lambda:None)),
                image_size=8,renderer_pixel_owner=True,_renderer_owner_annotators=owners,
                torch=torch,output=Path(folder),steps=0)
            result=RGBBackend._render_rgb_views(backend,require_calibration=True)
            self.assertEqual(set(result),set(views))
            self.assertEqual(set(backend._sensor_intrinsics),set(views))
            for view in views[:4]:
                self.assertIn("_renderer_instance_ids",backend._sensor_packets[view][0])
            for view in views[4:]:
                self.assertNotIn("_renderer_instance_ids",backend._sensor_packets[view][0])

if __name__=="__main__":unittest.main()
