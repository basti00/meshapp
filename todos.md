- any improvements to the design and UI? 
- any improvements to the displayed data per node and message? 
  should feel consistent, regardless what kind of update was received. 
- rebuild the app and db side to store all received frames per node in
  a seperate table. materialise the most recent ones when displaying info
  in the modal. also provide the age of that info (small font low 
  contrast, eg. „1h ago“) when clicking on a value show the received 
  frame in a new modal. might make sense to organize the info in blocks 
  based on the type of frame. (even if similar we wanna put non-message 
  frames in seperste table, incase we wanna fifo old data)
- redo the modals UI. first they should stack, when opening another 
  modal from within a modal. (like in z direction, with the lower ones 
  hidden by the top one) some low-key animation to show the change to 
  another modal. The x closes them one by one. clicking on the background 
  close all modals.
- node modals should show a lazy-scrollable list of all sent messages 
  (newest first, beginning of msg, datetime, channel) and another list 
  of all other sent frames (categorized by type, datetime, channel). click 
  on message or frame opens that modal. 
- [done] tapback reactions update the tree ordering. change it so only normal 
  replies update the ordering, not tapbacks
- [done] git commit every change seperatly after verifying (desktop and mobile) (add this instruction to claude.md )
