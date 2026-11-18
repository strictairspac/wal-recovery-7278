import {DRILL_STATUSES,BATCH_STATUSES} from '../../constants/enums.js';
import {esc} from '../../utils/html.js';
const LABELS={...DRILL_STATUSES,...BATCH_STATUSES};
export const badge=(status)=>`<span class="badge ${esc(status)}">${esc(LABELS[status]||status)}</span>`;
