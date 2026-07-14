"""IDM scaffold tests — pure pieces (no GPU, no video). Run:
  .venv\\Scripts\\python.exe -m pytest experiments/limbs/idm/test_idm.py -q
"""

import numpy as np

from experiments.limbs.idm.config import BUTTON_INDEX, KEY_INDEX, N_BUTTONS, N_KEYS
from experiments.limbs.idm.data import encode_action


def test_encode_action_keys_and_buttons():
    b = {"keys_down": ["w", "shift", "zzz_unknown"],
         "clicks": [("left", 10, 20), ("left", 11, 21)],
         "move_dx": 0, "move_dy": 0}
    t = encode_action(b)
    assert t["keys"][KEY_INDEX["w"]] == 1.0
    assert t["keys"][KEY_INDEX["shift"]] == 1.0
    assert t["keys"].sum() == 2.0            # unknown key ignored
    assert t["buttons"][BUTTON_INDEX["left"]] == 1.0
    assert t["buttons"][BUTTON_INDEX["right"]] == 0.0


def test_encode_action_camera_normalizes_and_clamps():
    from experiments.limbs.idm.config import CAMERA_SCALE

    # a mid-range delta normalizes proportionally
    t = encode_action({"keys_down": [], "clicks": [], "move_dx": 150, "move_dy": -75})
    assert abs(t["camera"][0] - 150 / CAMERA_SCALE) < 1e-6
    assert abs(t["camera"][1] - (-75 / CAMERA_SCALE)) < 1e-6
    # a huge flick clamps to the trained range, never explodes
    t2 = encode_action({"keys_down": [], "clicks": [], "move_dx": 99999, "move_dy": -99999})
    assert t2["camera"][0] == 1.0 and t2["camera"][1] == -1.0


def test_encode_action_empty_is_all_zero():
    t = encode_action({})
    assert t["keys"].sum() == 0 and t["buttons"].sum() == 0
    assert t["camera"][0] == 0 and t["camera"][1] == 0


def test_shapes_match_config():
    t = encode_action({"keys_down": ["w"], "clicks": [], "move_dx": 0, "move_dy": 0})
    assert t["keys"].shape == (N_KEYS,)
    assert t["buttons"].shape == (N_BUTTONS,)
    assert t["camera"].shape == (2,)


def test_cached_corpus_slices_and_samples(tmp_path):
    import numpy as np

    from experiments.limbs.idm.config import FRAME_SIZE, FRAMES_AFTER, FRAMES_BEFORE, N_KEYS, STACK
    from experiments.limbs.idm.precache import CachedCorpus

    # a synthetic shard: 12 frames, frame value == its index (so we can verify slicing)
    nframes = 12
    frames = np.stack([np.full((FRAME_SIZE, FRAME_SIZE), i, dtype=np.uint8) for i in range(nframes)])
    keys = np.zeros((nframes, N_KEYS), dtype=np.float32)
    keys[5, 0] = 1.0  # 'w' held on frame 5
    valid = np.arange(FRAMES_BEFORE, nframes - FRAMES_AFTER, dtype=np.int64)
    (tmp_path).mkdir(exist_ok=True)
    np.savez_compressed(tmp_path / "s.npz", frames=frames, keys=keys,
                        buttons=np.zeros((nframes, 2), dtype=np.float32),
                        camera=np.zeros((nframes, 2), dtype=np.float32), valid=valid)

    corpus = CachedCorpus(tmp_path)
    assert len(corpus) == len(valid)
    # the stack for center=5 is frames [3,4,5,6] scaled to [0,1]
    stack = corpus._stack(corpus.shards[0], 5)
    assert stack.shape == (STACK, FRAME_SIZE, FRAME_SIZE)
    assert abs(stack[0].mean() - 3 / 255) < 1e-4  # first frame in window is index 3
    assert abs(stack[FRAMES_BEFORE].mean() - 5 / 255) < 1e-4  # center is index 5
    # sample_batch returns the contracted shapes
    x, tgt = corpus.sample_batch(4)
    assert tuple(x.shape) == (4, STACK, FRAME_SIZE, FRAME_SIZE)
    assert tuple(tgt["keys"].shape) == (4, N_KEYS)


def test_model_forward_and_loss_shapes():
    import torch

    from experiments.limbs.idm.config import FRAME_SIZE, STACK
    from experiments.limbs.idm.model import IDM

    m = IDM()
    x = torch.zeros(3, STACK, FRAME_SIZE, FRAME_SIZE)
    out = m(x)
    assert out["keys"].shape == (3, N_KEYS)
    assert out["buttons"].shape == (3, N_BUTTONS)
    assert out["camera"].shape == (3, 2)
    target = {"keys": torch.zeros(3, N_KEYS), "buttons": torch.zeros(3, N_BUTTONS),
              "camera": torch.zeros(3, 2)}
    losses = m.loss(out, target)
    assert "total" in losses and losses["total"].item() >= 0
