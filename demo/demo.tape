# Sundial demo — record with: vhs demo/demo.tape  (brew install vhs)
# Produces demo/sundial.gif (~60s). Manual alternative: demo/RECORDING.md

Output demo/sundial.gif
Set FontSize 20
Set Width 1200
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms

Type "# Your agent just asked you something blocking. It arms the clock:"
Enter
Sleep 1s
Type "./bin/sundial ask 'should the header be sticky on mobile?'"
Enter
Sleep 2.5s

Type "./bin/sundial due"
Enter
Sleep 2.5s

Type "# Now you walk away. The ladder climbs on ABSENCE, not wall time:"
Enter
Type "#   10 min unseen -> popup+chime (Tink)   'A question ripened while you were away'"
Enter
Sleep 2s
Type "#   20 min -> popup (Glass)               'The kettle's been whistling 20 minutes'"
Enter
Sleep 2s
Type "#   50 min -> final (Hero, may speak)     '...proceeding on my judgment or standing down.'"
Enter
Sleep 2.5s

Type "# In another app instead? It knows, and it teases:"
Enter
Type "#   'I can see Figma has you. One opinion and I'll vanish.'"
Enter
Sleep 2.5s

Type "# Sitting right here reading? The clock pauses. Silence-while-present means 'not now'."
Enter
Sleep 2.5s

Type "# You come back -> ONE welcome-home nudge, then the moment you type anything:"
Enter
Sleep 1s
Type "./bin/sundial answered"
Enter
Sleep 2s
Type "./bin/sundial due"
Enter
Sleep 2s

Type "# Local-first. Zero deps. No LLM in the trigger path. github.com/bigjoe-oti/sundial"
Enter
Sleep 4s
