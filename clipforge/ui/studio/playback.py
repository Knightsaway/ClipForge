"""Watching the edit in the Studio: timeline clicks, Play whole edit, the final-look preview (text, GIFs, caster), the timeline toggle and the thumbnail moment."""
from __future__ import annotations

from PySide6.QtCore import QTimer

from ...config import save_settings
from ..widgets import fmt_dur


class PlaybackMixin:
    def _seek_out(self, out_t: float):
        """Timeline click: show that moment in the preview."""
        from ... import timeline_model as tm
        if not self.pieces:
            return
        i, t = tm.locate(self.pieces, out_t)
        if i < 0:
            return
        if i != self._current:
            self._autoplay_next = False
            self.list.setCurrentRow(i)
        self.player.player.setPosition(int(t * 1000))
        self.timeline.set_playhead(out_t)

    def _media_status(self, st):
        """A clip played right to the end of its file: in Play-whole-edit, carry on with the next one."""
        if self._play_all and st == self.player.player.MediaStatus.EndOfMedia and \
                not getattr(self, "_advancing", False):
            self._advancing = True
            QTimer.singleShot(400, lambda: setattr(self, "_advancing", False))
            if self._current + 1 < len(self.pieces):
                self.list.setCurrentRow(self._current + 1)
                self.player.player.play()
            else:
                self._toggle_play_all(stop=True)

    def _tab_changed(self, i: int):
        self.player.live_frames = i == 1
        if i == 1:
            self._refresh_cam_edit()

    def _live_tick(self, out_t: float, src_t: float, playing: bool):
        """Final-look preview: text / GIFs at their moments, the caster's lines play and the game dips."""
        prev = getattr(self, "_last_out", -1.0)
        self._last_out = out_t
        speaking = [c for c in self.caster_lines if c["t"] <= out_t <= c["t"] + c.get("dur", 1.5)]
        vol = self.caster_volume()
        if playing and vol > 0:
            for c in self.caster_lines:
                if prev < c["t"] <= out_t and out_t - prev < 0.6:
                    self._say(c, vol)
        if not playing:
            self._stop_say()
        self.player.duck(self.caster_duck.value() / 100 if (playing and speaking and vol > 0) else 1.0)
        a = self.hook_start
        self.cam_edit.set_live(playing, src_t, out_t, (a, a + self.hook_secs_eff()),
                               speaking[0].get("text", "") if speaking else "")

    def _player_pos(self, ms: int):
        from ... import timeline_model as tm
        pc = self._cur()
        if not pc:
            return
        lay = tm.layout(self.pieces)
        if self._current < len(lay):
            out_t = lay[self._current][0] + tm.src_to_out(pc, ms / 1000)
            self.timeline.set_playhead(out_t)
            playing = self.player.player.playbackState() == self.player.player.PlaybackState.PlayingState
            self._live_tick(out_t, ms / 1000, playing)
        end = min(pc["end"], (pc["clip"].duration or pc["end"]))
        if self._play_all and ms / 1000 >= end - 0.06 and not getattr(self, "_advancing", False):
            self._advancing = True
            QTimer.singleShot(400, lambda: setattr(self, "_advancing", False))
            if self._current + 1 < len(self.pieces):
                self.list.setCurrentRow(self._current + 1)
                self.player.player.play()
            else:
                self._toggle_play_all(stop=True)

    def _toggle_play_all(self, *_, stop: bool = False):
        if not self.pieces:
            return
        self._play_all = not self._play_all and not stop
        self.player.loop_range = not self._play_all
        self.play_all_btn.setText("⏹  Stop" if self._play_all else "▶  Play whole edit")
        if self._play_all:
            self.mid_tabs.setCurrentIndex(1)       # watch it the way it will look
            if self._current != 0:
                self.list.setCurrentRow(0)
            pc = self.pieces[0]
            self.player.player.setPosition(int(pc["start"] * 1000))
            self.player.player.play()
        else:
            self.player.player.pause()

    def _timeline_changed(self):
        self.cam_edit.set_items(self.items)
        cur = max(0, self._current)
        pc = self._cur()
        pos = self.player.player.position()
        was_playing = self.player.player.playbackState() == self.player.player.PlaybackState.PlayingState
        self._autoplay_next = was_playing
        self._refresh_list(cur)
        if pc and pc["start"] * 1000 <= pos <= pc["end"] * 1000:
            self.player.player.setPosition(pos)
        if pc:   # the clip may have been trimmed on the timeline: update the preview's handles
            self.player.timeline.t_in, self.player.timeline.t_out = pc["start"], pc["end"]
            self.player.timeline.update()
        self._show_fx()
        self.timeline.update()

    def _timeline_gif(self, i: int, gi: int):
        if i != self._current:
            self.list.setCurrentRow(i)
        self.mid_tabs.setCurrentIndex(1)
        self.cam_edit.select_gif(gi)

    def _advanced(self, on: bool):
        self.timeline.setVisible(on)
        self.tl_bar.setVisible(on)
        if self.s.studio_advanced != on:
            self.s.studio_advanced = on
            save_settings(self.s)

    def set_thumb(self, out_t: float):
        self.thumb_t = round(out_t, 2)
        self.timeline.update()
        self._mark()
        self.status.setText(f"🖼 The thumbnail will be made from {fmt_dur(out_t)} into the video")
