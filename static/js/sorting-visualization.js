// Configuration
const ARRAY_SIZE = 20;
const MIN_VALUE = 5;
const MAX_VALUE = 95;
let animationSpeed = 100; // ms

// State for each sort
const sortStates = {
  bubble: { array: [], isSorting: false, currentStep: 0 },
  selection: { array: [], isSorting: false, currentStep: 0 },
  insertion: { array: [], isSorting: false, currentStep: 0 },
  quick: { array: [], isSorting: false, currentStep: 0 },
  merge: { array: [], isSorting: false, currentStep: 0 }
};

// Initialize all visualizations
function initializeVisualizations() {
  Object.keys(sortStates).forEach(type => {
    resetArray(type);
  });
}

// Generate random array
function generateRandomArray() {
  return Array.from(
    { length: ARRAY_SIZE },
    () => Math.floor(Math.random() * (MAX_VALUE - MIN_VALUE + 1)) + MIN_VALUE
  );
}

// Create visualization bars
function createBars(type, array, highlights = []) {
  const container = document.getElementById(`${type}-sort-bars`);
  if (!container) return;
  
  container.innerHTML = '';
  const maxHeight = Math.max(...array, 1);
  
  array.forEach((value, index) => {
    const bar = document.createElement('div');
    const height = (value / maxHeight) * 100;
    bar.style.height = `${height}%`;
    bar.style.width = `${100 / array.length}%`;
    bar.style.backgroundColor = highlights.includes(index) ? '#e74c3c' : '#3498db';
    bar.style.transition = 'background-color 0.2s';
    bar.className = 'sort-bar';
    container.appendChild(bar);
  });
}

// Reset array for a specific sort type
function resetArray(type) {
  if (sortStates[type].isSorting) return;
  
  sortStates[type].array = generateRandomArray();
  sortStates[type].currentStep = 0;
  createBars(type, sortStates[type].array);
}

// Bubble Sort implementation
async function bubbleSort() {
  const { array } = sortStates.bubble;
  const n = array.length;
  let swapped;
  
  do {
    swapped = false;
    for (let i = 0; i < n - 1; i++) {
      if (array[i] > array[i + 1]) {
        [array[i], array[i + 1]] = [array[i + 1], array[i]];
        createBars('bubble', array, [i, i + 1]);
        await new Promise(resolve => setTimeout(resolve, animationSpeed));
        swapped = true;
      }
    }
  } while (swapped);
}

// Selection Sort implementation
async function selectionSort() {
  const { array } = sortStates.selection;
  const n = array.length;
  
  for (let i = 0; i < n - 1; i++) {
    let minIndex = i;
    
    for (let j = i + 1; j < n; j++) {
      if (array[j] < array[minIndex]) {
        minIndex = j;
      }
    }
    
    if (minIndex !== i) {
      [array[i], array[minIndex]] = [array[minIndex], array[i]];
      createBars('selection', array, [i, minIndex]);
      await new Promise(resolve => setTimeout(resolve, animationSpeed));
    }
  }
}

// Insertion Sort implementation
async function insertionSort() {
  const { array } = sortStates.insertion;
  const n = array.length;
  
  for (let i = 1; i < n; i++) {
    const key = array[i];
    let j = i - 1;
    
    while (j >= 0 && array[j] > key) {
      array[j + 1] = array[j];
      createBars('insertion', array, [j, j + 1]);
      await new Promise(resolve => setTimeout(resolve, animationSpeed));
      j--;
    }
    
    if (array[j + 1] !== key) {
      array[j + 1] = key;
      createBars('insertion', array, [j + 1]);
      await new Promise(resolve => setTimeout(resolve, animationSpeed));
    }
  }
}

// Event Listeners for all sort types
function setupSortButtons() {
  // Bubble Sort
  document.getElementById('bubble-sort-start')?.addEventListener('click', async () => {
    if (sortStates.bubble.isSorting) return;
    sortStates.bubble.isSorting = true;
    await bubbleSort();
    sortStates.bubble.isSorting = false;
  });
  
  document.getElementById('bubble-sort-reset')?.addEventListener('click', () => {
    resetArray('bubble');
  });

  // Selection Sort
  document.getElementById('selection-sort-start')?.addEventListener('click', async () => {
    if (sortStates.selection.isSorting) return;
    sortStates.selection.isSorting = true;
    await selectionSort();
    sortStates.selection.isSorting = false;
  });
  
  document.getElementById('selection-sort-reset')?.addEventListener('click', () => {
    resetArray('selection');
  });

  // Insertion Sort
  document.getElementById('insertion-sort-start')?.addEventListener('click', async () => {
    if (sortStates.insertion.isSorting) return;
    sortStates.insertion.isSorting = true;
    await insertionSort();
    sortStates.insertion.isSorting = false;
  });
  
  document.getElementById('insertion-sort-reset')?.addEventListener('click', () => {
    resetArray('insertion');
  });
}

// Initialize everything when the page loads
window.addEventListener('DOMContentLoaded', () => {
  initializeVisualizations();
  setupSortButtons();
});
