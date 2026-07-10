import { TestBed } from '@angular/core/testing';
import { App } from './app';
import { VideoReviewComponent } from './video-review.component';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render title', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('h1')?.textContent).toContain('Hello, video-uploader-app');
  });
});

describe('VideoReviewComponent zoom presets', () => {
  it('maps zoom presets to the expected square sizes', () => {
    const component = new VideoReviewComponent({ markForCheck() {} } as any);

    expect(component.getZoomPresetSquareSize(3.5)).toBe(200);
    expect(component.getZoomPresetSquareSize(3)).toBe(300);
    expect(component.getZoomPresetSquareSize(2)).toBe(350);
  });
});
