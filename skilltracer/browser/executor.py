"""Small BrowserGym adapter exposing observations and primitive actions."""

import logging

import gymnasium as gym
from browsergym.core.action.highlevel import HighLevelActionSet
from browsergym.utils.obs import flatten_axtree_to_str

from skilltracer.config import BROWSER_TIMEOUT_MS

logger = logging.getLogger(__name__)


class BrowsergymExecutor:
    def __init__(self, start_url, headless=True, timeout=None):
        self.env = None
        self.current_obs = None
        timeout = timeout or BROWSER_TIMEOUT_MS
        try:
            self.env = gym.make(
                "browsergym/openended",
                disable_env_checker=True,
                headless=headless,
                wait_for_user_message=False,
                action_mapping=HighLevelActionSet().to_python_code,
                task_kwargs={"start_url": start_url, "goal": None, "navigation_timeout": timeout},
                timeout=timeout,
            )
            self.current_obs, _ = self.env.reset()
        except Exception:
            self.close()
            raise

    def execute_browsergym_action(self, action):
        self.current_obs, _, terminated, truncated, _ = self.env.step(action)
        error = self.current_obs.get("last_action_error")
        if error:
            logger.warning("Browser action failed: %s", error)
        return not bool(error), terminated, truncated

    def get_browsergym_obs(self):
        return self.current_obs

    def refresh(self):
        self.current_obs = self.env.unwrapped._get_obs()

    def get_page_state(self):
        obs = self.current_obs or {}
        return {
            "url": obs.get("url", ""),
            "axtree_object": flatten_axtree_to_str(obs.get("axtree_object", {})),
            "dom_object": obs.get("dom_object", {}),
        }

    def close(self):
        if self.env is not None:
            self.env.close()
            self.env = None
